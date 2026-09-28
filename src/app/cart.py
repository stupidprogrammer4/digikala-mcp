"""Prepare bounded cart changes and execute each persisted plan at most once."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from src.infra.db.cart_journal import CartJournal
from src.infra.http import GatewayError
from src.infra.http.gateways.cart import CartGateway
from src.models.schemas.cart import (
    CartAction,
    CartChange,
    CartLimits,
    CartOperation,
    CartPlan,
    CartSnapshot,
)

CartGatewayFactory = Callable[[], AbstractAsyncContextManager[CartGateway]]


def snapshot_data(cart: CartSnapshot) -> dict:
    return cart.model_dump(mode="json", exclude={"observed_at"}, round_trip=True)


def same_cart(left: CartSnapshot, right: CartSnapshot) -> bool:
    a, b = snapshot_data(left), snapshot_data(right)
    for data in (a, b):
        data["items"].sort(key=lambda item: str(item["cart_item_id"]))
    return a == b


def exceeds(cart: CartSnapshot, limits: CartLimits | None) -> bool | None:
    if limits is None or cart.items_total_rial is None:
        return None
    return cart.total_items > limits.max_items or cart.items_total_rial > limits.max_total_rial


class CartService:
    def __init__(
        self, gateway_factory: CartGatewayFactory, journal: CartJournal, limits: CartLimits | None
    ):
        self.gateway_factory = gateway_factory
        self.journal = journal
        self.limits = limits

    async def read(self) -> CartSnapshot:
        async with self.gateway_factory() as gateway:
            return await gateway.read()

    async def _plan(self, gateway: CartGateway, cart: CartSnapshot, change: CartChange) -> CartPlan:
        if change.action == "add":
            if (
                not change.product_id
                or not change.offer_id
                or change.cart_item_id is not None
                or change.quantity is not None
            ):
                raise GatewayError("invalid_change", "Add needs product_id and offer_id only")
            if any(item.offer.offer_id == change.offer_id for item in cart.items):
                raise GatewayError("already_in_cart", "Use update for an existing cart item")
            selected = await gateway.get_offer(change.product_id, change.offer_id)
            previous, target, cart_item_id = 0, 1, None
            product_id, title, offer = selected.product_id, selected.title, selected.offer
        else:
            if (
                change.cart_item_id is None
                or change.product_id is not None
                or change.offer_id is not None
            ):
                raise GatewayError(
                    "invalid_change", "Update/remove need a cart_item_id from read_cart"
                )
            matches = [i for i in cart.items if str(i.cart_item_id) == str(change.cart_item_id)]
            if len(matches) != 1:
                raise GatewayError("cart_item_not_found", "Cart item is missing or ambiguous")
            item = matches[0]
            if change.action == "remove":
                if change.quantity is not None:
                    raise GatewayError("invalid_change", "Remove does not accept a quantity")
                target = 0
            else:
                if change.quantity is None:
                    raise GatewayError(
                        "invalid_change", "Update requires the final positive quantity"
                    )
                target = change.quantity
            previous, cart_item_id = item.quantity, item.cart_item_id
            product_id, title, offer = item.product_id, item.title, item.offer
            if target == previous:
                raise GatewayError("no_change", "The item already has that quantity")
            selected = None
            if target > previous:
                selected = await gateway.get_offer(product_id, offer.offer_id or "")
                offer, title = selected.offer, selected.title

        increasing = target > previous
        if increasing:
            if self.limits is None:
                raise GatewayError(
                    "cart_limits_required", "Configure both cart limits locally first"
                )
            if cart.has_unsupported_extras:
                raise GatewayError(
                    "unsupported_cart_extras", "Cannot budget insurance or extra services"
                )
            if offer.availability != "available" or offer.price_rial is None:
                raise GatewayError(
                    "offer_unavailable", "Selected offer must have a known price and stock"
                )
            assert selected is not None
            if target < selected.min_quantity or (
                selected.max_quantity is not None and target > selected.max_quantity
            ):
                raise GatewayError(
                    "quantity_unavailable", "Quantity is outside the seller's order limits"
                )

        total = cart.items_total_rial
        old_price = next(
            (i.offer.price_rial for i in cart.items if str(i.cart_item_id) == str(cart_item_id)), 0
        )
        if (
            total is not None
            and old_price is not None
            and (target == 0 or offer.price_rial is not None)
        ):
            total = total - previous * old_price + target * (offer.price_rial or 0)
        else:
            total = None
        count = cart.total_items - previous + target
        if increasing:
            assert self.limits is not None
            if total is None:
                raise GatewayError("unknown_cart_price", "All existing item prices must be known")
            if total > self.limits.max_total_rial:
                raise GatewayError(
                    "cart_amount_limit", "Projected item total exceeds the rial limit"
                )
            if count > self.limits.max_items:
                raise GatewayError(
                    "cart_quantity_limit", "Projected total units exceed the item limit"
                )
        return CartPlan(
            plan_id=uuid4().hex,
            action=change.action,
            product_id=product_id,
            title=title,
            offer=offer,
            cart_item_id=cart_item_id,
            previous_quantity=previous,
            target_quantity=target,
            projected_total_rial=total,
            projected_items=count,
            limits=self.limits,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )

    async def prepare(self, change: CartChange) -> CartPlan:
        async with self.gateway_factory() as gateway:
            if await self.journal.unresolved(gateway.connection_id):
                raise GatewayError("cart_outcome_unknown", "Resolve the previous operation first")
            cart = await gateway.read()
            plan = await self._plan(gateway, cart, change)
            await self.journal.insert(
                {
                    "connection_id": gateway.connection_id,
                    "state": "prepared",
                    "plan": plan.model_dump(mode="json"),
                    "before": snapshot_data(cart),
                    "change": change.model_dump(mode="json"),
                }
            )
            return plan

    async def preview(self, plan_id: str, action: CartAction) -> CartPlan:
        plan = CartPlan.model_validate((await self.journal.get(plan_id))["plan"])
        if plan.action != action:
            raise GatewayError("wrong_action", "Plan belongs to a different cart operation")
        return plan

    @staticmethod
    def _matches(plan: CartPlan, before: CartSnapshot, after: CartSnapshot) -> bool:
        wanted = {i.offer.offer_id: i.quantity for i in before.items}
        if plan.target_quantity:
            wanted[plan.offer.offer_id] = plan.target_quantity
        else:
            wanted.pop(plan.offer.offer_id, None)
        return wanted == {i.offer.offer_id: i.quantity for i in after.items}

    async def _reconcile(self, gateway: CartGateway, record: dict) -> CartOperation:
        plan = CartPlan.model_validate(record["plan"])
        try:
            after = await gateway.read()
        except GatewayError:
            return CartOperation(plan_id=plan.plan_id, state="uncertain", reason="read_back_failed")
        matched = self._matches(plan, CartSnapshot.model_validate(record["before"]), after)
        breached = exceeds(after, plan.limits)
        return CartOperation(
            plan_id=plan.plan_id,
            state="applied" if matched else "uncertain",
            cart=after,
            reason=("limits_exceeded_after_write" if breached else None)
            if matched
            else "cart_does_not_match_expected_quantities",
            limits_exceeded=breached,
        )

    async def execute(self, plan_id: str, action: CartAction) -> CartOperation:
        record = await self.journal.get(plan_id)
        plan = CartPlan.model_validate(record["plan"])
        if plan.action != action:
            raise GatewayError("wrong_action", "Plan belongs to a different cart operation")
        async with self.gateway_factory() as gateway:
            if record["connection_id"] != gateway.connection_id:
                raise GatewayError("connection_changed", "Prepare again for the connected account")
            if record["state"] in {"applied", "rejected"}:
                return CartOperation.model_validate(record["result"])
            if record["state"] == "prepared":
                # Commit exclusive execution ownership via a unique account constraint and CAS.
                claimed = await self.journal.transition(record, "prepared", "executing")
                if claimed is None:
                    return await self._existing(plan_id)
                record = claimed
                try:
                    if datetime.now(timezone.utc) >= plan.expires_at:
                        raise GatewayError("plan_expired", "Prepare a fresh cart change")
                    before = await gateway.read()
                    if not same_cart(before, CartSnapshot.model_validate(record["before"])):
                        raise GatewayError("cart_changed", "Cart changed; prepare again")
                    fresh = await self._plan(
                        gateway, before, CartChange.model_validate(record["change"])
                    )
                    ignored = {"plan_id", "expires_at"}
                    if fresh.model_dump(exclude=ignored) != plan.model_dump(exclude=ignored):
                        raise GatewayError(
                            "offer_or_limits_changed", "Offer or limits changed; prepare again"
                        )
                except GatewayError as exc:
                    result = CartOperation(plan_id=plan_id, state="rejected", reason=exc.error.code)
                    return await self._finish(record, result)
                try:
                    await gateway.mutate(plan)
                except GatewayError:
                    pass  # The upstream may have applied a timed-out request.
            elif record["state"] == "executing":
                # The owner may still be sending a request. Never reconcile or release it here.
                return CartOperation(
                    plan_id=plan_id,
                    state="uncertain",
                    reason="execution_in_progress_or_interrupted",
                )
            result = await self._reconcile(gateway, record)
            return await self._finish(record, result)

    async def _existing(self, plan_id: str) -> CartOperation:
        record = await self.journal.get(plan_id)
        if record.get("result") is not None:
            return CartOperation.model_validate(record["result"])
        return CartOperation(
            plan_id=plan_id, state="uncertain", reason="execution_in_progress_or_interrupted"
        )

    async def _finish(self, record: dict, result: CartOperation) -> CartOperation:
        record["result"] = result.model_dump(mode="json", round_trip=True)
        saved = await self.journal.transition(record, record["state"], result.state)
        return result if saved else await self._existing(result.plan_id)
