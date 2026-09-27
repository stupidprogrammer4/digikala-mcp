"""Cart policy and durable write safety. All products, account IDs and carts are synthetic."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest

from src.app.cart import CartService
from src.infra.cart_journal import CartJournal
from src.infra.gateways.cart import CartGateway
from src.infra.http import GatewayError
from src.models import Availability, Offer
from src.models.cart import CartChange, CartItem, CartLimits, CartOffer, CartSnapshot


def offer(price: int | None = 100):
    return Offer(
        offer_id="10",
        variant_id="10",
        seller_id="20",
        price_rial=price,
        availability="available",
        attributes={"color": "black"},
        warranty="test",
    )


def item(quantity=1, price: int | None = 100):
    return CartItem(
        cart_item_id=5, product_id="1", title="Test", quantity=quantity, offer=offer(price)
    )


class FakeCart(CartGateway):
    connection_id = "test-account-session"

    def __init__(self):
        self.cart = CartSnapshot()
        self.price = 100
        self.writes = []
        self.failure = None
        self.max_quantity = 9
        self.min_quantity = 1
        self.stock: Availability = "available"

    async def read(self):
        return self.cart.model_copy(deep=True)

    async def get_offer(self, product_id, offer_id):
        chosen = offer(self.price)
        chosen.availability = self.stock
        return CartOffer(
            product_id=product_id,
            title="Test",
            offer=chosen,
            min_quantity=self.min_quantity,
            max_quantity=self.max_quantity,
        )

    async def mutate(self, plan):
        self.writes.append(plan.action)
        if self.failure == "before":
            raise GatewayError("timeout", "test")
        if plan.action == "add":
            self.cart.items.append(item())
        elif plan.action == "update":
            self.cart.items[0].quantity = plan.target_quantity
        else:
            self.cart.items.clear()
        if self.failure == "after":
            raise GatewayError("timeout", "test")


@pytest.fixture
def cart_setup(tmp_path):
    gateway = FakeCart()

    @asynccontextmanager
    async def open_gateway():
        yield gateway

    service = CartService(
        open_gateway, CartJournal(tmp_path), CartLimits(max_total_rial=300, max_items=3)
    )
    return service, gateway


def add():
    return CartChange(action="add", product_id="1", offer_id="10")


async def test_post_patch_delete_at_exact_limits(cart_setup):
    service, gateway = cart_setup
    plan = await service.prepare(add())
    assert plan.projected_total_rial == 100
    assert gateway.writes == []
    result = await service.execute(plan.plan_id, "add")
    assert result.state == "applied" and result.cart.total_items == 1
    plan = await service.prepare(CartChange(action="update", cart_item_id=5, quantity=3))
    assert plan.projected_total_rial == 300 and plan.projected_items == 3
    result = await service.execute(plan.plan_id, "update")
    assert result.state == "applied" and result.cart.total_items == 3
    plan = await service.prepare(CartChange(action="remove", cart_item_id=5))
    result = await service.execute(plan.plan_id, "remove")
    assert result.state == "applied" and result.cart.total_items == 0
    assert gateway.writes == ["add", "update", "remove"]


@pytest.mark.parametrize(
    "amount,count,code", [(99, 3, "cart_amount_limit"), (300, 0, "cart_quantity_limit")]
)
async def test_both_limits_block_without_writes(cart_setup, amount, count, code):
    service, gateway = cart_setup
    service.limits = CartLimits(max_total_rial=amount, max_items=count)
    with pytest.raises(GatewayError) as caught:
        await service.prepare(add())
    assert caught.value.error.code == code
    assert gateway.writes == []


async def test_quantity_cap_counts_units_and_other_existing_lines(cart_setup):
    service, gateway = cart_setup
    other = item(quantity=3)
    other.cart_item_id = 9
    other.offer.offer_id = "99"
    gateway.cart.items.append(other)
    service.limits = CartLimits(max_total_rial=1000, max_items=3)
    with pytest.raises(GatewayError, match="total units"):
        await service.prepare(add())
    assert gateway.writes == []


@pytest.mark.parametrize("action", ["update", "remove"])
async def test_decreases_allowed_without_limits_and_unknown_prices(cart_setup, action):
    service, gateway = cart_setup
    service.limits = None
    gateway.cart.items = [item(3, None)]
    plan = await service.prepare(
        CartChange(action=action, cart_item_id=5, quantity=2 if action == "update" else None)
    )
    result = await service.execute(plan.plan_id, action)
    assert result.state == "applied"


async def test_no_limits_no_increases(cart_setup):
    service, gateway = cart_setup
    service.limits = None
    with pytest.raises(GatewayError, match="limits"):
        await service.prepare(add())
    assert gateway.writes == []


@pytest.mark.parametrize("change", ["price", "cart", "limits", "stock", "expiry", "seller_limit"])
async def test_revalidation_rejects_stale_plans(cart_setup, change):
    service, gateway = cart_setup
    plan = await service.prepare(add())
    if change == "price":
        gateway.price += 1
    elif change == "cart":
        gateway.cart.items = [item()]
    elif change == "limits":
        service.limits = CartLimits(max_total_rial=90, max_items=3)
    elif change == "stock":
        gateway.stock = "unavailable"
    elif change == "seller_limit":
        gateway.min_quantity = 2
    else:
        with service.journal.locked() as db:
            record = service.journal.get(db, plan.plan_id)
            record["plan"]["expires_at"] = (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat()
            service.journal.put(db, record)
    result = await service.execute(plan.plan_id, "add")
    assert result.state == "rejected"
    assert gateway.writes == []


async def test_timeout_applied_is_reconciled_and_never_replayed_after_restart(cart_setup):
    service, gateway = cart_setup
    gateway.failure = "after"
    plan = await service.prepare(add())
    assert (await service.execute(plan.plan_id, "add")).state == "applied"
    restarted = CartService(service.gateway_factory, service.journal, service.limits)
    assert (await restarted.execute(plan.plan_id, "add")).state == "applied"
    assert gateway.writes == ["add"]


async def test_uncertain_write_blocks_other_plans_and_never_retries(cart_setup):
    service, gateway = cart_setup
    first = await service.prepare(add())
    second = await service.prepare(add())
    gateway.failure = "before"
    assert (await service.execute(first.plan_id, "add")).state == "uncertain"
    assert (await service.execute(first.plan_id, "add")).state == "uncertain"
    with pytest.raises(GatewayError, match="previous operation"):
        await service.execute(second.plan_id, "add")
    with pytest.raises(GatewayError, match="previous operation"):
        await service.prepare(add())
    assert gateway.writes == ["add"]
    gateway.cart.items = [item()]  # Later read observes that the timed-out request was applied.
    assert (await service.execute(first.plan_id, "add")).state == "applied"
    assert gateway.writes == ["add"]


async def test_wrong_method_and_account_cannot_use_plan(cart_setup):
    service, gateway = cart_setup
    plan = await service.prepare(add())
    with pytest.raises(GatewayError, match="different cart operation"):
        await service.execute(plan.plan_id, "remove")
    gateway.connection_id = "another-account"
    with pytest.raises(GatewayError, match="connected account"):
        await service.execute(plan.plan_id, "add")
    assert gateway.writes == []


async def test_concurrent_executions_send_one_write(cart_setup):
    service, gateway = cart_setup
    plan = await service.prepare(add())
    results = await asyncio.gather(
        service.execute(plan.plan_id, "add"), service.execute(plan.plan_id, "add")
    )
    assert all(result.state == "applied" for result in results)
    assert gateway.writes == ["add"]


async def test_unknown_existing_price_blocks_increase(cart_setup):
    service, gateway = cart_setup
    gateway.cart.items = [item(price=None)]
    with pytest.raises(GatewayError, match="prices must be known"):
        await service.prepare(CartChange(action="update", cart_item_id=5, quantity=2))
    assert gateway.writes == []


def test_separate_process_locks_conflict(tmp_path):
    first, second = CartJournal(tmp_path), CartJournal(tmp_path)
    with first.locked():
        with pytest.raises(GatewayError, match="Another local process"):
            with second.locked():
                pytest.fail("Second journal should not acquire the lock")


async def test_cart_tools_over_mcp(cart_setup):
    from mcp import Client

    from src.server import create_server

    service, gateway = cart_setup
    async with Client(create_server(cart_service=service)) as client:
        limits = await client.call_tool("get_cart_limits")
        assert limits.structured_content["limits"]["max_items"] == 3
        cart = await client.call_tool("read_cart")
        assert cart.structured_content["total_items"] == 0
        for change, tool in [
            ({"action": "add", "product_id": "1", "offer_id": "10"}, "add_to_cart"),
            ({"action": "update", "cart_item_id": 5, "quantity": 2}, "update_cart_item"),
            ({"action": "remove", "cart_item_id": 5}, "remove_from_cart"),
        ]:
            preview = await client.call_tool("prepare_cart_change", {"change": change})
            assert not preview.is_error
            result = await client.call_tool(
                tool, {"plan_id": preview.structured_content["plan_id"]}
            )
            assert not result.is_error
            assert result.structured_content["state"] == "applied"
        assert gateway.writes == ["add", "update", "remove"]
        invalid = await client.call_tool(
            "prepare_cart_change",
            {"change": {"action": "update", "cart_item_id": 5, "quantity": 0}},
        )
        assert invalid.is_error


async def test_exceeded_limits_after_upstream_change_are_reported(cart_setup):
    service, gateway = cart_setup
    plan = await service.prepare(add())
    original = gateway.mutate

    async def repriced(plan):
        await original(plan)
        gateway.cart.items[0].offer.price_rial = 400

    gateway.mutate = repriced
    result = await service.execute(plan.plan_id, "add")
    assert result.state == "applied"
    assert result.limits_exceeded is True
    assert result.reason == "limits_exceeded_after_write"
    assert gateway.writes == ["add"]
