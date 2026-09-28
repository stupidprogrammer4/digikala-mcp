"""Repository durability and SQLModel session cleanup on PostgreSQL."""

import pytest
from sqlalchemy.exc import OperationalError

from src.infra.db import DBConnection
from src.infra.db.exceptions import DatabaseUnavailable
from src.models.db import CartOperationRow
from tests.database_scopes import JournalClient
from tests.test_cart import add
from tests.test_journal import record


async def test_clean_session_exit_discards_uncommitted_rows(journal):
    async with journal.database.session() as session:
        session.add(
            CartOperationRow(
                id="uncommitted",
                connection="account",
                state="prepared",
                record=record("uncommitted"),
            )
        )
        await session.flush()
        assert await journal.find("uncommitted") is None
    assert await journal.find("uncommitted") is None


async def test_failed_commit_is_sanitized_and_connection_can_be_reused(journal, monkeypatch):
    async def broken_commit(session):
        await session.flush()
        raise OperationalError("private SQL", {"password": "secret"}, Exception("private"))

    with monkeypatch.context() as patch:
        patch.setattr(journal.database.session_factory.class_, "commit", broken_commit)
        with pytest.raises(DatabaseUnavailable) as caught:
            await journal.insert(record("commit-failure"))
        assert "private" not in str(caught.value) and "secret" not in str(caught.value)
    assert await journal.find("commit-failure") is None
    await journal.insert(record("after-failure"))
    assert await journal.find("after-failure") is not None


async def test_claim_is_committed_and_connection_released_before_storefront_write(cart_setup):
    service, gateway = cart_setup
    plan = await service.prepare(add())
    observer = DBConnection(service.journal.database._dsn)
    original = gateway.mutate
    observed = []

    async def inspect_then_mutate(plan):
        stored = await JournalClient(observer).get(plan.plan_id)
        observed.append(stored["state"])
        # All short application transactions have returned their connections.
        pool = service.journal.database.engine.pool
        assert getattr(pool, "checkedout")() == 0
        await original(plan)

    gateway.mutate = inspect_then_mutate
    try:
        result = await service.execute(plan.plan_id, "add")
    finally:
        await observer.aclose()
    assert result.state == "applied" and observed == ["executing"]
    assert gateway.writes == ["add"]
