"""Authenticated Digikala cart API; each caller owns an isolated HTTP client."""

from abc import ABC, abstractmethod

from src.infra.gateways.digikala import parse_offer
from src.infra.gateways.parsing import checked_schema, items
from src.infra.http import GatewayError, HTTPConnection
from src.models.cart import CartItem, CartOffer, CartPlan, CartSnapshot

CART_PATH = "/checkout/v1/carts/items"
CART_HEADERS = {"x-site": "DIGIKALA", "x-source": "WEB"}


class CartGateway(ABC):
    connection_id: str

    @abstractmethod
    async def read(self) -> CartSnapshot: ...

    @abstractmethod
    async def get_offer(self, product_id: str, offer_id: str) -> CartOffer: ...

    @abstractmethod
    async def mutate(self, plan: CartPlan) -> None: ...


class DigikalaCartGateway(CartGateway):
    def __init__(self, http: HTTPConnection, connection_id: str):
        self.http = http
        self.connection_id = connection_id

    async def verify_session(self) -> None:
        body = await self.http.request("GET", "/v1/user/init/", params={"skip_keys[]": "cart"})
        if body.get("status") != 200 or (body.get("data") or {}).get("is_logged_in") is not True:
            raise GatewayError("session_expired", "Log in again with the local account command")

    @checked_schema
    async def read(self) -> CartSnapshot:
        await self.verify_session()
        body = await self.http.request("GET", "/checkout/v1/carts/mini", headers=CART_HEADERS)
        if body["status"] != 200 or body.get("success") is False:
            raise GatewayError("cart_read_failed", "Could not read the authenticated cart")
        data = body["data"]
        cart = data["cart"]
        raw_items = data.get("cart_items")
        if raw_items is None:
            raw_items = [
                item for package in items(data["packages"]) for item in items(package["cart_items"])
            ]
        parsed = []
        extras = bool(cart.get("insurance") or cart.get("temporary_plus_subscription"))
        for raw in items(raw_items):
            variant = raw["variant"]
            offer = parse_offer(variant)
            # Preserve absence of price as unknown. Cart variant prices are unit prices.
            parsed.append(
                CartItem(
                    cart_item_id=raw["id"],
                    product_id=str(raw["product"]["id"]),
                    title=raw["product"]["title_fa"],
                    quantity=raw["quantity"],
                    offer=offer,
                )
            )
            extras |= bool(raw.get("has_insurance") or raw.get("has_dk_service"))
        if len({str(i.cart_item_id) for i in parsed}) != len(parsed):
            raise ValueError("Duplicate cart item")
        if len({i.offer.offer_id for i in parsed}) != len(parsed):
            raise ValueError("Duplicate cart offer")
        result = CartSnapshot(
            items=parsed,
            shipping_price_rial=cart.get("shipping_cost"),
            has_unsupported_extras=extras,
        )
        # Fail closed if the response omits some of the cart (e.g. a new package schema).
        count = cart["items_count"]
        if type(count) is not int or count != result.total_items:
            raise ValueError("Cart count mismatch")
        if (data.get("fresh") or {}).get("cart_items"):
            raise ValueError("Separate fresh cart is not supported")
        return result

    @checked_schema
    async def get_offer(self, product_id: str, offer_id: str) -> CartOffer:
        body = await self.http.request("GET", f"/product/v1/products/{product_id}/")
        if body["status"] != 200:
            raise GatewayError("offer_unavailable", "Product could not be refreshed")
        product = body["data"]["product"]
        if str(product["id"]) != product_id:
            raise ValueError("Product identity mismatch")
        candidates = [v for v in items(product["variants"]) if str(v["id"]) == offer_id]
        if len(candidates) != 1:
            raise GatewayError("offer_unavailable", "Selected seller offer is missing or ambiguous")
        variant = candidates[0]
        price = variant.get("price") or {}
        return CartOffer(
            product_id=product_id,
            title=product["title_fa"],
            offer=parse_offer(variant),
            min_quantity=price.get("min_order_limit", 1),
            max_quantity=price.get("order_limit"),
        )

    async def mutate(self, plan: CartPlan) -> None:
        if plan.action == "add":
            method = "POST"
            payload = {"variant_ids": [int(plan.offer.offer_id or "")], "pass": True}
        elif plan.action == "update":
            method = "PATCH"
            payload = {"cartItemId": plan.cart_item_id, "quantity": plan.target_quantity}
        else:
            method = "DELETE"
            payload = {"cartItemIds": [plan.cart_item_id]}
        body = await self.http.request(method, CART_PATH, json=payload, headers=CART_HEADERS)
        if body.get("status") != 200 or body.get("success") is False:
            raise GatewayError("cart_write_rejected", "Store did not acknowledge the cart change")
