"""Real PostgreSQL tests for atomic claims and fail-closed cart execution."""

import asyncio
import json
import os
import sys

import pytest

from src.app.cart import CartService
from src.infra.db import CartJournal, Database
from src.infra.db.exceptions import (
    DatabaseNotConfigured,
    DatabaseUnavailable,
    OperationConflict,
    UnresolvedOperation,
)
from src.infra.http import GatewayError
from tests.test_cart import add


def record(key, connection="account", state="prepared"):
    return {"plan": {"plan_id": key}, "connection_id": connection, "state": state}


async def test_claim_is_atomic_across_independent_database_connections(journal):
    first = await journal.insert(record("a"))
    other_database = Database(journal.database._dsn)
    other = CartJournal(other_database)
    claims = await asyncio.gather(
        journal.transition(first, "prepared", "executing"),
        other.transition(first, "prepared", "executing"),
    )
    await other_database.aclose()
    assert sum(c is not None for c in claims) == 1
    assert (await journal.get("a"))["state"] == "executing"


async def test_one_unresolved_operation_per_account_and_stale_updates_fail(journal):
    a = await journal.insert(record("a"))
    b = await journal.insert(record("b"))
    owned = await journal.transition(a, "prepared", "executing")
    assert owned is not None
    with pytest.raises(UnresolvedOperation) as caught:
        await journal.transition(b, "prepared", "executing")
    assert caught.value.code == "cart_outcome_unknown"
    c = await journal.insert(record("c", connection="another-account", state="executing"))
    assert c["state"] == "executing"
    assert await journal.transition(a, "prepared", "rejected") is None
    finished = await journal.transition(owned, "executing", "applied")
    assert finished is not None
    assert await journal.transition(b, "prepared", "executing") is not None


async def test_duplicate_id_is_not_overwritten(journal):
    await journal.insert(record("a"))
    with pytest.raises(OperationConflict) as caught:
        await journal.insert(record("a", connection="another-account"))
    assert caught.value.code == "request_conflict"
    assert (await journal.get("a"))["connection_id"] == "account"


async def test_unconfigured_database_has_sanitized_error():
    with pytest.raises(DatabaseNotConfigured) as caught:
        await CartJournal(Database(None)).get("id")
    assert caught.value.code == "database_not_configured"


async def test_inflight_retry_cannot_release_ownership_or_resend(cart_setup):
    service, gateway = cart_setup
    plan = await service.prepare(add())
    started, release = asyncio.Event(), asyncio.Event()
    original = gateway.mutate

    async def slow(plan):
        started.set()
        await release.wait()
        await original(plan)

    gateway.mutate = slow
    owner = asyncio.create_task(service.execute(plan.plan_id, "add"))
    await started.wait()
    restarted = CartService(service.gateway_factory, service.journal, service.limits)
    retry = await restarted.execute(plan.plan_id, "add")
    assert retry.state == "uncertain"
    assert retry.reason == "execution_in_progress_or_interrupted"
    assert (await service.journal.get(plan.plan_id))["state"] == "executing"
    with pytest.raises(GatewayError):
        await restarted.prepare(add())
    release.set()
    assert (await owner).state == "applied"
    assert gateway.writes == ["add"]


async def test_interrupted_executor_does_not_allow_new_write(cart_setup):
    service, gateway = cart_setup
    plan = await service.prepare(add())
    started = asyncio.Event()

    async def interrupted(plan):
        started.set()
        await asyncio.Event().wait()

    gateway.mutate = interrupted
    task = asyncio.create_task(service.execute(plan.plan_id, "add"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await service.execute(plan.plan_id, "add")).state == "uncertain"
    assert (await service.journal.get(plan.plan_id))["state"] == "executing"
    assert gateway.writes == []


async def test_claim_is_atomic_across_processes(journal):
    await journal.insert(record("process-test"))
    code = """
import asyncio, json, os
from src.infra.db import CartJournal, Database
async def main():
    database = Database(os.environ["TEST_JOURNAL_DSN"])
    journal = CartJournal(database)
    row = await journal.get("process-test")
    result = None
    if row["state"] == "prepared":
        result = await journal.transition(row, "prepared", "executing")
    await database.aclose()
    print(json.dumps({"claimed": result is not None}))
asyncio.run(main())
"""
    assert journal.database._dsn is not None
    environment = dict(os.environ, TEST_JOURNAL_DSN=journal.database._dsn)

    async def run():
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            code,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await process.communicate()
        assert process.returncode == 0, err.decode()
        return json.loads(out)["claimed"]

    assert sum(await asyncio.gather(run(), run())) == 1


async def test_database_failure_after_write_preserves_executing_record(cart_setup, monkeypatch):
    service, gateway = cart_setup
    plan = await service.prepare(add())
    transition = service.journal.transition

    async def fail_finish(record, expected_state, state):
        if expected_state == "executing":
            raise DatabaseUnavailable()
        return await transition(record, expected_state, state)

    monkeypatch.setattr(service.journal, "transition", fail_finish)
    with pytest.raises(DatabaseUnavailable):
        await service.execute(plan.plan_id, "add")
    assert gateway.writes == ["add"]
    assert (await service.journal.get(plan.plan_id))["state"] == "executing"
    monkeypatch.setattr(service.journal, "transition", transition)
    assert (await service.execute(plan.plan_id, "add")).state == "uncertain"
    assert gateway.writes == ["add"]
