import httpx
from fastmcp import Client

from src.server import create_server


async def test_trend_snapshot_keeps_upstream_order_without_inventing_history(
    payload, catalog_factory
):
    from contextlib import asynccontextmanager

    product = payload("digikala-product")["data"]["product"]

    def handle(request):
        assert request.url.path == "/v1/"
        return httpx.Response(
            200,
            json={
                "status": 200,
                "data": {"best_selling_products": {"title": "پرفروش‌ترین‌ها", "products": [product]}},
            },
        )

    @asynccontextmanager
    async def factory():
        yield catalog_factory(handle)

    async with Client(create_server(catalog_factory=factory)) as client:
        response = await client.call_tool("get_trend_snapshot", raise_on_error=False)
    assert response.structured_content is not None
    data = response.structured_content
    assert not response.is_error and data["error"] is None
    assert [p["product_id"] for p in data["products"]] == [str(product["id"])]
    assert data["period"] is None
    assert data["source"] == "digikala_home_best_selling"
    assert data["observed_at"]


async def test_missing_trend_listing_returns_explicit_error(catalog_factory):
    service = catalog_factory(lambda request: httpx.Response(200, json={"status": 200, "data": {}}))
    result = await service.trends()
    assert result.error is not None
    assert result.products == []
