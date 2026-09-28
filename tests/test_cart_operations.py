"""Recovery never replays a mutation or takes ownership from an active executor."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastmcp import Client

from src.app.accounts import AccountService, ConnectedAccount
from src.app.cart_operations import inspect_operation
from src.app.replacement import CartReplacer
from src.infra.http import GatewayError
from src.infra.http.account_login import PasswordLogin
from src.models.schemas.cart import CartChange
from src.server import create_server
from tests.test_cart import FakeCart, add, item


async def test_status_and_reconciliation_never_execute_prepared_plan(cart_setup):
    service, gateway = cart_setup
    plan = await service.prepare(add())
    result = await inspect_operation(service, plan.plan_id, reconcile=True)
    assert result.state == "prepared" and not result.expired
    assert gateway.writes == []
    record = await service.journal.get(plan.plan_id)
    record["plan"]["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    await service.journal.transition(record, "prepared", "prepared")
    result = await inspect_operation(service, plan.plan_id)
    assert result.expired
    assert "connection_id" not in result.model_dump_json()


async def test_reconciliation_keeps_mismatch_blocked_then_confirms_without_write(cart_setup):
    service, gateway = cart_setup
    plan = await service.prepare(add())
    gateway.failure = "before"
    result = await service.execute(plan.plan_id, "add")
    assert result.state == "uncertain"
    result = await inspect_operation(service, plan.plan_id, reconcile=True)
    assert result.state == "uncertain"
    gateway.cart.items = [item()]
    results = await asyncio.gather(
        *(inspect_operation(service, plan.plan_id, reconcile=True) for _ in range(3))
    )
    assert all(r.state == "applied" for r in results)
    assert gateway.writes == ["add"]
    assert not await service.journal.unresolved(gateway.connection_id)


async def test_reconciliation_does_not_release_executing_owner(cart_setup):
    service, gateway = cart_setup
    plan = await service.prepare(add())
    record = await service.journal.get(plan.plan_id)
    await service.journal.transition(record, "prepared", "executing")
    gateway.cart.items = [item()]
    result = await inspect_operation(service, plan.plan_id, reconcile=True)
    assert result.state == "executing"
    assert result.reason == "execution_in_progress_or_interrupted"
    assert await service.journal.unresolved(gateway.connection_id)
    assert gateway.writes == []


async def test_status_cannot_expose_another_account(cart_setup):
    service, gateway = cart_setup
    plan = await service.prepare(add())
    gateway.connection_id = "other-account"
    for reconcile in (True, False):
        with pytest.raises(GatewayError) as caught:
            await inspect_operation(service, plan.plan_id, reconcile=reconcile)
        assert caught.value.error.code == "plan_not_found"
    assert gateway.writes == []


async def test_local_operation_tools_through_mcp(cart_setup):
    service, gateway = cart_setup
    plan = await service.prepare(add())
    gateway.failure = "before"
    await service.execute(plan.plan_id, "add")
    async with Client(create_server(cart_service=service)) as client:
        status = await client.call_tool("get_cart_operation", {"operation_id": plan.plan_id})
        assert status.data.state == "uncertain"
        gateway.cart.items = [item()]
        result = await client.call_tool("reconcile_cart_operation", {"operation_id": plan.plan_id})
        assert result.data.state == "applied"
    assert gateway.writes == ["add"]


async def test_token_account_incremental_tools_isolation_expiry_and_replay(cart_setup):
    service, alice = cart_setup
    bob = FakeCart()
    bob.connection_id = "bob"
    stores = {"alice": alice, "bob": bob}

    @asynccontextmanager
    async def gateways(session):
        yield stores[session.connection_id]

    accounts = AccountService(
        PasswordLogin(), gateways, CartReplacer(service.journal, service.limits)
    )
    for token in stores:
        accounts.sessions[token] = ConnectedAccount(
            token, httpx.Cookies(), datetime.now(timezone.utc) + timedelta(minutes=5)
        )
    async with Client(create_server(account_service=accounts)) as client:
        prepared = await client.call_tool(
            "prepare_account_cart_change",
            {
                "session_token": "alice",
                "change": add().model_dump(),
            },
        )
        plan_id = prepared.data.plan_id
        for name, args in [
            ("get_account_cart_operation", {"operation_id": plan_id}),
            ("reconcile_account_cart_operation", {"operation_id": plan_id}),
            ("add_to_account_cart", {"plan_id": plan_id}),
        ]:
            denied = await client.call_tool(
                name, {"session_token": "bob", **args}, raise_on_error=False
            )
            assert denied.is_error
        assert bob.writes == alice.writes == []
        args = {"session_token": "alice", "plan_id": plan_id}
        for _ in range(2):
            result = await client.call_tool("add_to_account_cart", args)
            assert result.data.state == "applied"
        assert alice.writes == ["add"]
        status = await client.call_tool(
            "get_account_cart_operation",
            {
                "session_token": "alice",
                "operation_id": plan_id,
            },
        )
        assert status.data.state == "applied"
        for action, change, tool in [
            (
                "update",
                CartChange(action="update", cart_item_id=5, quantity=2),
                "update_account_cart_item",
            ),
            ("remove", CartChange(action="remove", cart_item_id=5), "remove_from_account_cart"),
        ]:
            prepared = await client.call_tool(
                "prepare_account_cart_change",
                {
                    "session_token": "alice",
                    "change": change.model_dump(),
                },
            )
            result = await client.call_tool(
                tool, {"session_token": "alice", "plan_id": prepared.data.plan_id}
            )
            assert result.data.state == "applied"
        assert alice.writes == ["add", "update", "remove"]
        accounts.sessions["alice"].expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        expired = await client.call_tool(
            "get_account_cart_operation",
            {
                "session_token": "alice",
                "operation_id": plan_id,
            },
            raise_on_error=False,
        )
        assert expired.is_error


async def test_token_cart_caps_cannot_be_overridden(cart_setup):
    service, gateway = cart_setup

    @asynccontextmanager
    async def gateways(session):
        yield gateway

    accounts = AccountService(
        PasswordLogin(), gateways, CartReplacer(service.journal, service.limits)
    )
    accounts.sessions["token"] = ConnectedAccount(
        "test", httpx.Cookies(), datetime.now(timezone.utc) + timedelta(minutes=5)
    )
    gateway.cart.items = [item(quantity=3)]
    with pytest.raises(GatewayError) as caught:
        await accounts.cart("token").prepare(
            CartChange(action="update", cart_item_id=5, quantity=4)
        )
    assert caught.value.error.code == "cart_amount_limit"
    assert gateway.writes == []
