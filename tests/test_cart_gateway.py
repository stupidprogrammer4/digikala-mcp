"""Synthetic payloads derived from the storefront contract; never a private cart capture."""

import json
from datetime import datetime, timezone

import httpx
import pytest

from src.infra.gateways.cart import CART_PATH, DigikalaCartGateway
from src.infra.http import GatewayError, HTTPConnection
from src.models import Offer
from src.models.cart import CartPlan


def cart_body(quantity=2):
    return {
        "status": 200,
        "success": True,
        "data": {
            "cart": {"items_count": quantity},
            "packages": [
                {
                    "address": {"phone": "PRIVATE"},
                    "cart_items": [
                        {
                            "id": 5,
                            "quantity": quantity,
                            "product": {"id": 1, "title_fa": "Test"},
                            "variant": {
                                "id": 10,
                                "seller": {"id": 20},
                                "price": {"selling_price": 100},
                                "status": "marketable",
                            },
                        }
                    ],
                }
            ],
        },
    }


async def test_cart_units_prices_and_personal_data_are_separate():
    def handler(request):
        if request.url.path.endswith("init/"):
            return httpx.Response(200, json={"status": 200, "data": {"is_logged_in": True}})
        assert request.url.path == "/checkout/v1/carts/mini"
        assert request.headers["x-site"] == "DIGIKALA"
        return httpx.Response(200, json=cart_body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await DigikalaCartGateway(
            HTTPConnection(client, "https://api.digikala.com"), "test"
        ).read()
    assert result.total_items == 2 and result.items_total_rial == 200
    assert result.shipping_price_rial is None
    assert result.items[0].cart_item_id == 5
    assert result.items[0].offer.offer_id == "10"
    assert "PRIVATE" not in result.model_dump_json()


@pytest.mark.parametrize(
    "method,payload,action,target",
    [
        ("POST", {"variant_ids": [10], "pass": True}, "add", 1),
        ("PATCH", {"cartItemId": 5, "quantity": 3}, "update", 3),
        ("DELETE", {"cartItemIds": [5]}, "remove", 0),
    ],
)
async def test_exact_mutation_contract(method, payload, action, target):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.path == CART_PATH
        assert request.method == method
        assert json.loads(request.content) == payload
        assert request.headers["x-source"] == "WEB"
        return httpx.Response(200, json={"status": 200, "success": True})

    plan = CartPlan(
        plan_id="a" * 32,
        action=action,
        product_id="1",
        title="Test",
        offer=Offer(offer_id="10"),
        cart_item_id=5,
        previous_quantity=2,
        target_quantity=target,
        projected_total_rial=100,
        projected_items=target,
        limits=None,
        expires_at=datetime.now(timezone.utc),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await DigikalaCartGateway(
            HTTPConnection(client, "https://api.digikala.com"), "test"
        ).mutate(plan)
    assert len(requests) == 1


@pytest.mark.parametrize("kind", ["expired", "missing_packages", "omitted_items", "bad_price"])
async def test_session_and_incomplete_cart_fail_closed(kind):
    def handler(request):
        if request.url.path.endswith("init/"):
            return httpx.Response(
                200, json={"status": 200, "data": {"is_logged_in": kind != "expired"}}
            )
        data = cart_body()
        if kind == "missing_packages":
            del data["data"]["packages"]
        elif kind == "omitted_items":
            data["data"]["cart"]["items_count"] = 3
        elif kind == "bad_price":
            data["data"]["packages"][0]["cart_items"][0]["variant"]["price"]["selling_price"] = (
                "100"
            )
        return httpx.Response(200, json=data)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GatewayError):
            await DigikalaCartGateway(
                HTTPConnection(client, "https://api.digikala.com"), "test"
            ).read()


async def test_empty_cart_shape_from_live_probe():
    def handler(request):
        if request.url.path.endswith("init/"):
            return httpx.Response(200, json={"status": 200, "data": {"is_logged_in": True}})
        return httpx.Response(
            200,
            json={
                "status": 200,
                "success": True,
                "data": {"cart": {"items_count": 0}, "packages": []},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await DigikalaCartGateway(
            HTTPConnection(client, "https://api.digikala.com"), "test"
        ).read()
    assert result.items == [] and result.items_total_rial == 0


async def test_offer_is_exact_and_carries_seller_quantity_limits(payload):
    def handler(request):
        return httpx.Response(200, json=payload("digikala-product"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = DigikalaCartGateway(HTTPConnection(client, "https://api.digikala.com"), "test")
        chosen = await gateway.get_offer("22672438", "84016750")
        assert chosen.max_quantity == 5 and chosen.min_quantity == 1
        assert chosen.offer.price_rial == 27999900
        with pytest.raises(GatewayError):
            await gateway.get_offer("22672438", "999")
