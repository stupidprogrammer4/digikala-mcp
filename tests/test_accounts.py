from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from fastmcp import Client

from src.app.accounts import AccountService
from src.app.replacement import CartReplacer
from src.infra.http import GatewayError
from src.infra.http.account_login import LoginOutcome, PasswordLogin
from src.infra.http.gateways.cart import CartGateway
from src.models.schemas.account import CartReplacement, SelectedCartOffer
from src.models.schemas.cart import CartItem, CartLimits, CartOffer, CartSnapshot
from src.models.schemas.product import Offer
from src.server import create_server


def offer(identifier="11", price=100):
    return Offer(
        offer_id=identifier,
        variant_id=identifier,
        seller_id="1",
        seller_name="Test seller",
        price_rial=price,
        availability="available",
    )


class Store(CartGateway):
    def __init__(self, identity):
        self.connection_id = identity
        self.items = [
            CartItem(
                cart_item_id="old", product_id="99", title="Existing", quantity=1, offer=offer("99")
            )
        ]
        self.calls = []
        self.fail_add = False
        self.price = 100

    async def read(self):
        return CartSnapshot(items=list(self.items))

    async def get_offer(self, product_id, offer_id):
        return CartOffer(product_id=product_id, title="Jewelry", offer=offer(offer_id, self.price))

    async def mutate(self, plan):
        self.calls.append(plan.action)
        if plan.action == "remove":
            self.items = [i for i in self.items if i.cart_item_id != plan.cart_item_id]
        elif self.fail_add:
            raise GatewayError("network_error", "Connection lost")
        else:
            assert plan.offer.offer_id is not None
            self.items.append(
                CartItem(
                    cart_item_id=plan.offer.offer_id,
                    product_id=plan.product_id,
                    title=plan.title,
                    quantity=1,
                    offer=plan.offer,
                )
            )


class Login(PasswordLogin):
    def login(self, credentials):
        if credentials.password.get_secret_value() == "otp":
            return LoginOutcome(
                "challenge_required", "phone_confirmation_required", httpx.Cookies()
            )
        cookies = httpx.Cookies({"test_user": credentials.username.get_secret_value()})
        return LoginOutcome("connected", None, cookies)


def replacement():
    return CartReplacement(
        request_id=uuid4(),
        items=[
            SelectedCartOffer(product_id="1", offer_id="11", seller_id="1", expected_price_rial=100)
        ],
        max_total_rial=200,
    )


@pytest.fixture
def setup(journal):
    stores = {"alice": Store("alice"), "bob": Store("bob")}

    @asynccontextmanager
    async def gateway(session):
        yield stores[session.cookies.get("test_user")]

    service = AccountService(
        Login(),
        gateway,
        CartReplacer(journal, CartLimits(max_total_rial=200, max_items=3)),
    )
    return create_server(account_service=service), service, stores


async def login(client, username, password="test-password"):
    result = await client.call_tool(
        "login_account",
        {"credentials": {"username": username, "password": password}},
        raise_on_error=False,
    )
    assert not result.is_error
    assert result.structured_content is not None
    return result.structured_content


async def test_two_accounts_replace_only_selected_cart_and_replay(setup):
    server, _, stores = setup
    async with Client(server) as client:
        alice = await login(client, "alice")
        bob = await login(client, "bob")
        assert alice["session_token"] != bob["session_token"]
        request = replacement()
        args = {
            "session_token": alice["session_token"],
            "replacement": request.model_dump(mode="json"),
        }
        result = await client.call_tool("replace_account_cart", args, raise_on_error=False)
        assert result.structured_content is not None
        assert result.structured_content["state"] == "applied"
        assert [i.product_id for i in stores["alice"].items] == ["1"]
        assert [i.product_id for i in stores["bob"].items] == ["99"]
        again = await client.call_tool("replace_account_cart", args, raise_on_error=False)
        assert again.structured_content is not None
        assert result.structured_content is not None
        assert again.structured_content == result.structured_content
        assert stores["alice"].calls == ["remove", "add"]
        wrong = await client.call_tool(
            "replace_account_cart",
            {**args, "session_token": bob["session_token"]},
            raise_on_error=False,
        )
        assert wrong.structured_content is not None
        assert wrong.structured_content["reason"] == "request_conflict"
        assert stores["bob"].calls == []


async def test_expired_and_unknown_tokens_never_fall_back_to_local_account(setup):
    server, service, stores = setup
    async with Client(server) as client:
        logged = await login(client, "alice")
        token = logged["session_token"]
        service.sessions[token].expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        expired = await client.call_tool(
            "read_account_cart", {"session_token": token}, raise_on_error=False
        )
        unknown = await client.call_tool(
            "read_account_cart", {"session_token": "bad"}, raise_on_error=False
        )
        assert expired.is_error and unknown.is_error
        assert all(not s.calls for s in stores.values())


async def test_phone_challenge_does_not_issue_token(setup):
    server, _, _ = setup
    async with Client(server) as client:
        result = await login(client, "alice", "otp")
        assert result["state"] == "challenge_required"
        assert result["session_token"] is None


async def test_changed_price_prevents_clearing_cart(setup):
    server, _, stores = setup
    async with Client(server) as client:
        account = await login(client, "alice")
        stores["alice"].price = 101
        result = await client.call_tool(
            "replace_account_cart",
            {
                "session_token": account["session_token"],
                "replacement": replacement().model_dump(mode="json"),
            },
            raise_on_error=False,
        )
        assert result.structured_content is not None
        assert result.structured_content["reason"] == "offer_changed"
        assert stores["alice"].calls == []
        assert stores["alice"].items[0].product_id == "99"


async def test_partial_failure_is_not_replayed_and_blocks_new_replacement(setup):
    server, _, stores = setup
    async with Client(server) as client:
        account = await login(client, "alice")
        stores["alice"].fail_add = True
        args = {
            "session_token": account["session_token"],
            "replacement": replacement().model_dump(mode="json"),
        }
        result = await client.call_tool("replace_account_cart", args, raise_on_error=False)
        assert result.structured_content is not None
        assert result.structured_content["state"] == "uncertain"
        again = await client.call_tool("replace_account_cart", args, raise_on_error=False)
        assert again.structured_content is not None
        assert result.structured_content is not None
        assert again.structured_content == result.structured_content
        fresh = await client.call_tool(
            "replace_account_cart",
            {**args, "replacement": replacement().model_dump(mode="json")},
            raise_on_error=False,
        )
        assert fresh.structured_content is not None
        assert fresh.structured_content["reason"] == "cart_outcome_unknown"
        assert stores["alice"].calls == ["remove", "add"]


async def test_logout_invalidates_only_one_token(setup):
    server, _, _ = setup
    async with Client(server) as client:
        alice = await login(client, "alice")
        bob = await login(client, "bob")
        await client.call_tool(
            "logout_account", {"session_token": alice["session_token"]}, raise_on_error=False
        )
        first = await client.call_tool(
            "read_account_cart", {"session_token": alice["session_token"]}, raise_on_error=False
        )
        second = await client.call_tool(
            "read_account_cart", {"session_token": bob["session_token"]}, raise_on_error=False
        )
        assert first.is_error and not second.is_error


async def test_replacement_reconciliation_never_repeats_partial_batch(setup):
    from src.app.cart_operations import inspect_operation
    from src.models.schemas.account import AccountLogin

    _, service, stores = setup
    connected = await service.login(
        AccountLogin.model_validate(
            {
                "username": "alice",
                "password": "test-password",
            }
        )
    )
    assert connected.session_token is not None
    request = replacement()
    stores["alice"].fail_add = True
    result = await service.replacer.replace(stores["alice"], request)
    assert result.state == "uncertain"
    operations = service.cart(connected.session_token)
    status = await inspect_operation(operations, request.request_id.hex, reconcile=True)
    assert status.state == "uncertain" and status.kind == "replacement"
    stores["alice"].items = [
        CartItem(
            cart_item_id="11",
            product_id="1",
            title="Jewelry",
            quantity=1,
            offer=offer(),
        )
    ]
    status = await inspect_operation(operations, request.request_id.hex, reconcile=True)
    assert status.state == "applied"
    assert stores["alice"].calls == ["remove", "add"]
    retry = await service.replacer.replace(stores["alice"], request)
    assert retry.state == "applied"
    assert not await service.replacer.journal.unresolved("alice")


async def test_legacy_replacement_does_not_guess_missing_selection(setup):
    from src.app.cart_operations import inspect_operation
    from src.models.schemas.account import AccountLogin

    _, service, stores = setup
    connected = await service.login(
        AccountLogin.model_validate(
            {
                "username": "alice",
                "password": "test-password",
            }
        )
    )
    assert connected.session_token is not None
    request = replacement()
    stores["alice"].fail_add = True
    await service.replacer.replace(stores["alice"], request)
    record = await service.replacer.journal.get(request.request_id.hex)
    record.pop("request")
    await service.replacer.journal.transition(record, "uncertain", "uncertain")
    status = await inspect_operation(
        service.cart(connected.session_token), request.request_id.hex, reconcile=True
    )
    assert status.state == "uncertain"
    assert status.reason == "legacy_operation_requires_manual_review"
    assert stores["alice"].calls == ["remove", "add"]
