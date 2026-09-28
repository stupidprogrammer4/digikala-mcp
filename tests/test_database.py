"""SQLModel persistence, lifecycle, rollback and compatibility on isolated PostgreSQL schemas."""

import asyncio

import pytest
from fastmcp import Client
from sqlalchemy import event
from sqlmodel import col, select

from src.app.accounts import AccountService
from src.app.cart import CartService
from src.bootstrap import create_container
from src.infra.db import DBConnection
from src.infra.db.connection import connection_options
from src.infra.db.exceptions import (
    DatabaseClosed,
    DatabaseConfigurationError,
    DatabaseIntegrityError,
    DatabaseUnavailable,
    OperationConflict,
    OperationNotFound,
)
from src.infra.db.repositories import CartJournal
from src.models.db import CartOperationRow
from src.server import create_server
from tests.database_migrations import migrate_database
from tests.database_scopes import JournalClient
from tests.test_cart import add
from tests.test_journal import record


@pytest.mark.parametrize("scheme", ["postgres", "postgresql", "postgresql+psycopg"])
def test_url_driver_and_sensitive_characters_are_preserved(scheme):
    url, options = connection_options(
        f"{scheme}://user:pass%40word@localhost:5432/cart?sslmode=require"
    )
    assert url.drivername == "postgresql+psycopg"
    assert url.password == "pass@word"
    assert url.query["sslmode"] == "require"
    assert options["connect_timeout"] == 5


@pytest.mark.parametrize("dsn", ["sqlite:///tmp/not-postgres", "broken password=private-secret"])
async def test_bad_configuration_is_lazy_and_sanitized(dsn):
    database = DBConnection(dsn)
    try:
        with pytest.raises(DatabaseConfigurationError) as caught:
            await JournalClient(database).find("id")
        assert "private-secret" not in str(caught.value)
        assert dsn not in str(caught.value)
    finally:
        await database.aclose()


async def test_missing_schema_is_not_created_by_repository(database):
    with pytest.raises(DatabaseUnavailable):
        await JournalClient(database).find("missing")
    await migrate_database(database)
    with pytest.raises(OperationNotFound):
        await JournalClient(database).get("missing")


async def test_sqlmodel_round_trip_and_repository_snapshots_do_not_alias(journal):
    source = record("typed")
    source["nested"] = {"values": [1, 2]}
    stored = await journal.insert(source)
    source["nested"]["values"].append(3)
    stored["nested"]["values"].append(4)
    async with journal.database.session() as session:
        row = (
            await session.exec(select(CartOperationRow).where(col(CartOperationRow.id) == "typed"))
        ).one()
        assert isinstance(row, CartOperationRow)
        assert row.updated_at is not None and row.updated_at.tzinfo is not None
        assert row.revision == 0 and row.record["nested"] == {"values": [1, 2]}
    assert (await journal.get("typed"))["nested"] == {"values": [1, 2]}


async def test_integrity_error_rolls_back_and_next_operation_gets_a_clean_session(journal):
    await journal.insert(record("one"))
    with pytest.raises(OperationConflict):
        await journal.insert(record("one"))
    await journal.insert(record("two"))
    assert (await journal.get("two"))["state"] == "prepared"
    with pytest.raises(DatabaseIntegrityError) as caught:
        async with journal.database.session() as session:
            session.add(
                CartOperationRow(
                    id="mismatch",
                    connection="account",
                    state="prepared",
                    record={**record("mismatch"), "state": "applied"},
                )
            )
            await session.flush()
    assert caught.value.sqlstate == "23514"
    assert "mismatch" not in str(caught.value)
    assert await journal.find("mismatch") is None


async def test_exception_and_cancellation_roll_back_uncommitted_rows(journal):
    with pytest.raises(RuntimeError, match="abort"):
        async with journal.database.session() as session:
            session.add(
                CartOperationRow(
                    id="abort", connection="a", state="prepared", record=record("abort", "a")
                )
            )
            await session.flush()
            raise RuntimeError("abort")
    assert await journal.find("abort") is None
    started = asyncio.Event()

    async def transaction():
        async with journal.database.session() as session:
            session.add(
                CartOperationRow(
                    id="cancel", connection="a", state="prepared", record=record("cancel", "a")
                )
            )
            await session.flush()
            started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(transaction())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await journal.find("cancel") is None
    await journal.insert(record("after-cancel"))


async def test_sessions_release_connections_and_disposal_prevents_reuse(journal):
    database = journal.database
    counts = {"checkout": 0, "checkin": 0}

    def checkout(*_):
        counts["checkout"] += 1

    def checkin(*_):
        counts["checkin"] += 1

    event.listen(database.engine.sync_engine, "checkout", checkout)
    event.listen(database.engine.sync_engine, "checkin", checkin)
    await asyncio.gather(*(journal.find(str(i)) for i in range(8)))
    assert counts["checkout"] == counts["checkin"] == 8
    await database.aclose()
    with pytest.raises(DatabaseClosed):
        await journal.find("id")


async def test_dishka_owns_one_database_for_both_cart_paths_and_disposes_it():
    container = create_container()
    cart = await container.get(CartService)
    accounts = await container.get(AccountService)
    database = await container.get(DBConnection)
    assert cart.journal is accounts.replacer.journal
    assert await container.get(DBConnection) is database
    await container.close()
    with pytest.raises(DatabaseClosed):
        _ = database.engine


async def test_database_failure_after_remote_write_is_not_reported_as_rejection(
    cart_setup, monkeypatch
):
    service, gateway = cart_setup
    plan = await service.prepare(add())
    original = CartJournal.transition

    async def fail_finish(self, record, expected_state, state):
        if expected_state == "executing":
            raise DatabaseUnavailable()
        return await original(self, record, expected_state, state)

    monkeypatch.setattr(CartJournal, "transition", fail_finish)
    async with Client(create_server(cart_service=service)) as client:
        result = await client.call_tool(
            "add_to_cart", {"plan_id": plan.plan_id}, raise_on_error=False
        )
        assert result.is_error and result.structured_content is None
        assert "database_unavailable" in str(result.content)
    assert gateway.writes == ["add"]
    assert (await service.journal.get(plan.plan_id))["state"] == "executing"


async def test_existing_psycopg_schema_and_records_survive_baseline_adoption(database):
    # Historical schema, deliberately not generated from the new model.
    legacy_schema = """
    CREATE TABLE cart_operations (
        id TEXT PRIMARY KEY,
        connection TEXT NOT NULL,
        state TEXT NOT NULL
            CHECK (state IN ('prepared','executing','uncertain','applied','rejected')),
        revision BIGINT NOT NULL DEFAULT 0,
        record JSONB NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CHECK (record->>'state' = state),
        CHECK (record->>'connection_id' = connection),
        CHECK (record->'plan'->>'plan_id' = id)
    );
    CREATE UNIQUE INDEX cart_one_unresolved_operation ON cart_operations (connection)
    WHERE state IN ('executing', 'uncertain');
    INSERT INTO cart_operations(id, connection, state, revision, record)
    VALUES ('legacy', 'account', 'executing', 7,
            '{"plan":{"plan_id":"legacy"},"connection_id":"account","state":"executing"}');
    """
    async with database.connection() as connection:
        await connection.exec_driver_sql(legacy_schema)
    await migrate_database(database, "stamp", "0001_cart_journal")
    await migrate_database(database)
    await migrate_database(database)
    journal = JournalClient(database)
    row = await journal.get("legacy")
    assert row["_revision"] == 7 and row["state"] == "executing"
    updated = await journal.transition(row, "executing", "applied")
    assert updated is not None and updated["_revision"] == 8
    assert await journal.transition(row, "executing", "rejected") is None
