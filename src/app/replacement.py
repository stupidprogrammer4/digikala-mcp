import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256

from src.app.cart import same_cart
from src.infra.db.cart_journal import CartJournal
from src.infra.db.exceptions import OperationConflict
from src.infra.http import GatewayError
from src.infra.http.gateways.cart import CartGateway
from src.models.schemas.account import CartReplacement, CartReplacementResult
from src.models.schemas.cart import CartLimits, CartPlan, CartSnapshot


class CartReplacer:
    def __init__(self, journal: CartJournal, limits: CartLimits | None):
        self.journal, self.limits = journal, limits

    @staticmethod
    def matches(request: CartReplacement, cart: CartSnapshot) -> bool:
        wanted = {(i.product_id, i.offer_id, i.seller_id): 1 for i in request.items}
        actual = {
            (i.product_id, i.offer.offer_id, i.offer.seller_id): i.quantity for i in cart.items
        }
        return (
            wanted == actual
            and not cart.has_unsupported_extras
            and cart.items_total_rial is not None
            and cart.items_total_rial <= request.max_total_rial
        )

    async def replace(
        self, gateway: CartGateway, request: CartReplacement
    ) -> CartReplacementResult:
        fingerprint = sha256(request.model_dump_json().encode()).hexdigest()
        key = request.request_id.hex
        record = await self.journal.find(key)
        if record is not None:
            return self._existing(record, gateway.connection_id, fingerprint, request)
        try:
            record = await self.journal.insert(
                {
                    "plan": {"plan_id": key},
                    "connection_id": gateway.connection_id,
                    "fingerprint": fingerprint,
                    "request": request.model_dump(mode="json"),
                    "limits": self.limits.model_dump() if self.limits else None,
                    "state": "executing",
                }
            )
        except OperationConflict:
            return self._existing(
                await self.journal.get(key), gateway.connection_id, fingerprint, request
            )
        try:
            before = await gateway.read()
            if before.has_unsupported_extras:
                raise GatewayError("unsupported_cart_extras", "Cart has unsupported services")
            if self.limits is None:
                raise GatewayError("cart_limits_required", "Configure trusted host limits")
            if (
                request.max_total_rial > self.limits.max_total_rial
                or len(request.items) > self.limits.max_items
            ):
                raise GatewayError("cart_limits_exceeded", "Requested cart exceeds host limits")
            offers = await asyncio.gather(
                *(gateway.get_offer(i.product_id, i.offer_id) for i in request.items)
            )
            for selected, current in zip(request.items, offers, strict=True):
                offer = current.offer
                if (
                    offer.seller_id != selected.seller_id
                    or offer.offer_id != selected.offer_id
                    or offer.price_rial != selected.expected_price_rial
                    or offer.availability != "available"
                    or not offer.price_rial
                    or current.min_quantity > 1
                    or (current.max_quantity is not None and current.max_quantity < 1)
                ):
                    raise GatewayError(
                        "offer_changed", "Refresh the exact selection before replacing cart"
                    )
            if sum(i.expected_price_rial for i in request.items) > request.max_total_rial:
                raise GatewayError("cart_amount_limit", "Selected total exceeds the request budget")
        except GatewayError as error:
            result = CartReplacementResult(
                request_id=request.request_id, state="rejected", reason=error.error.code
            )
            record["result"] = result.model_dump(mode="json", round_trip=True)
            await self.journal.transition(record, "executing", "rejected")
            return result
        expected = before
        try:
            for item in before.items:
                current = await gateway.read()
                if not same_cart(current, expected):
                    raise GatewayError("cart_changed", "Cart changed outside this operation")
                plan = CartPlan(
                    plan_id=key,
                    action="remove",
                    product_id=item.product_id,
                    title=item.title,
                    offer=item.offer,
                    cart_item_id=item.cart_item_id,
                    previous_quantity=item.quantity,
                    target_quantity=0,
                    projected_total_rial=None,
                    projected_items=0,
                    limits=self.limits,
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                )
                await gateway.mutate(plan)
                expected = expected.model_copy(
                    update={
                        "items": [i for i in expected.items if i.cart_item_id != item.cart_item_id]
                    }
                )
            for selected in request.items:
                current = await gateway.read()
                if not same_cart(current, expected):
                    raise GatewayError("cart_changed", "Cart changed outside this operation")
                fresh = await gateway.get_offer(selected.product_id, selected.offer_id)
                if (
                    fresh.offer.price_rial != selected.expected_price_rial
                    or fresh.offer.availability != "available"
                    or fresh.offer.seller_id != selected.seller_id
                ):
                    raise GatewayError("offer_changed", "Offer changed during replacement")
                plan = CartPlan(
                    plan_id=key,
                    action="add",
                    product_id=selected.product_id,
                    title=fresh.title,
                    offer=fresh.offer,
                    previous_quantity=0,
                    target_quantity=1,
                    projected_total_rial=None,
                    projected_items=0,
                    limits=self.limits,
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                )
                await gateway.mutate(plan)
                after = await gateway.read()
                expected_quantities = {i.offer.offer_id: i.quantity for i in expected.items}
                expected_quantities[selected.offer_id] = 1
                if expected_quantities != {i.offer.offer_id: i.quantity for i in after.items}:
                    raise GatewayError("cart_changed", "Written cart does not match the selection")
                expected = after
            after = await gateway.read()
            result = CartReplacementResult(
                request_id=request.request_id,
                state="applied" if self.matches(request, after) else "uncertain",
                cart=after,
                reason=None if self.matches(request, after) else "cart_mismatch",
            )
        except GatewayError as error:
            result = CartReplacementResult(
                request_id=request.request_id, state="uncertain", reason=error.error.code
            )
        record.update(state=result.state, result=result.model_dump(mode="json", round_trip=True))
        await self.journal.transition(record, "executing", result.state)
        return result

    @staticmethod
    def _existing(record, connection_id, fingerprint, request) -> CartReplacementResult:
        if record["connection_id"] != connection_id or record.get("fingerprint") != fingerprint:
            raise GatewayError(
                "request_conflict", "Request belongs to another account or selection"
            )
        if record.get("result") is not None:
            return CartReplacementResult.model_validate(record["result"])
        return CartReplacementResult(
            request_id=request.request_id,
            state="uncertain",
            reason="execution_in_progress_or_interrupted",
        )
