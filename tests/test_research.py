"""Public response contracts and bounded research tools. Chart samples are synthetic."""

import copy
from contextlib import asynccontextmanager

import httpx
import pytest
from fastmcp import Client
from pydantic import ValidationError

from src.models.schemas import SearchQuery
from src.models.schemas.research import FilterSelection, ProductBatchQuery, ProductComparisonQuery
from src.server import create_server


@pytest.fixture
def research(catalog_factory, payload):
    calls = []
    original = payload("digikala-product-content")
    raw = original["data"]["product"]
    raw["images"] = {
        "main": {"url": ["https://dkstatics-public.digikala.com/main.jpg"]},
        "list": [
            {"url": ["https://dkstatics-public.digikala.com/main.jpg"]},
            {"url": ["https://dkstatics-public.digikala.com/second.jpg"]},
        ],
    }
    raw["videos"] = [
        {
            "url": "https://video.digikala.com/video.mp4",
            "title": "Demo",
            "cover": "https://video.digikala.com/cover.jpg",
        }
    ]

    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        assert "cookie" not in request.headers
        if request.url.path.endswith("search"):
            return httpx.Response(200, json=payload("digikala-filters"))
        if request.url.path.endswith("price-chart/"):
            return httpx.Response(
                200,
                json={
                    "status": 200,
                    "data": {
                        "chart_selection_title": "Color",
                        "price_chart": [
                            {
                                "title": "Black",
                                "history": [
                                    {
                                        "day": "1405/07/01",
                                        "selling_price": 100,
                                        "rrp_price": 120,
                                        "is_marketable": True,
                                        "seller": "A",
                                        "product_warranty": "1 year",
                                    },
                                    {
                                        "day": "1405/07/03",
                                        "selling_price": 0,
                                        "is_marketable": False,
                                    },
                                ],
                            },
                        ],
                    },
                },
            )
        identifier = request.url.path.strip("/").split("/")[-1]
        if identifier == "3":
            return httpx.Response(404)
        body = copy.deepcopy(original)
        body["data"]["product"]["id"] = int(identifier)
        body["data"]["product"]["specifications"] = [
            {
                "attributes": [
                    {"title": "Storage", "values": ["128 GB" if identifier == "1" else "256 GB"]},
                    {"title": "Weight", "values": ["100 g"]},
                ]
                + ([{"title": "Color", "values": ["Black"]}] if identifier == "1" else [])
            }
        ]
        return httpx.Response(200, json=body)

    return catalog_factory(handler), calls


async def test_batch_partial_failure_order_and_cache(research):
    catalog, calls = research
    query = ProductBatchQuery(product_ids=["2", "3", "1"])
    result = await catalog.products.batch(query)
    assert list(result.results) == ["2", "3", "1"]
    assert result.results["3"].error.code == "not_found"
    assert result.results["1"].product.product_id == "1"
    first = result.results["1"].product.observed_at
    result.results["1"].product.title = "changed"
    repeated = await catalog.products.batch(query)
    assert repeated.results["1"].product.observed_at == first
    assert repeated.results["1"].product.title != "changed"
    assert len(calls) == 4  # Only errors are fetched again.


async def test_comparison_literal_values_and_missing_fields(research):
    catalog, _ = research
    result = await catalog.products.compare_products(ProductComparisonQuery(product_ids=["1", "2"]))
    rows = {r.title: r for r in result.specifications}
    assert rows["Storage"].relation == "different"
    assert rows["Weight"].relation == "same"
    assert rows["Color"].relation == "unknown" and rows["Color"].values["2"] is None
    missing = await catalog.products.compare_products(
        ProductComparisonQuery(product_ids=["1", "3"])
    )
    assert all(r.relation == "unknown" for r in missing.specifications)


@pytest.mark.parametrize("identifiers", [[], ["1", "1"], ["../1"], [str(i) for i in range(1, 22)]])
def test_invalid_batch(identifiers):
    with pytest.raises(ValidationError):
        ProductBatchQuery(product_ids=identifiers)


async def test_media_deduplication_and_detail_cache(research):
    catalog, calls = research
    result = await catalog.products.media("1")
    assert result.error is None
    assert len(result.media) == 3 and result.media[0].is_main
    assert result.media[-1].kind == "video"
    batch = await catalog.products.batch(ProductBatchQuery(product_ids=["1"]))
    assert result.observed_at == batch.results["1"].product.observed_at
    assert len(calls) == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://video.digikala.com/a",
        "https://evil.test/a",
        "https://video.digikala.com@evil.test/a",
        "file:///tmp/a",
    ],
)
async def test_media_rejects_unexpected_origins(url, catalog_factory, payload):
    body = payload("digikala-product-content")
    body["data"]["product"]["images"] = {"main": {"url": [url]}}
    catalog = catalog_factory(lambda _: httpx.Response(200, json=body))
    result = await catalog.products.media("22672438")
    assert result.error.code == "schema_changed"


async def test_filters_validate_values_and_encode_supported_parameters(research):
    catalog, calls = research
    filters = await catalog.products.filters("11")
    assert filters.error is None
    by_key = {f.key: f for f in filters.filters}
    assert "18" in {o.value_id for o in by_key["brands"].options}
    assert len(by_key["brands"].options) == len({o.value_id for o in by_key["brands"].options})
    query = SearchQuery(
        category_id="11",
        filters=FilterSelection(
            values={
                "brands": ["18"],
                "attribute_7291": ["31223"],
                "color_palettes": ["12"],
            }
        ),
    )
    result = await catalog.search(query)
    assert result.results[0].error is None
    assert calls[-1].url.params["brands[0]"] == "18"
    assert calls[-1].url.params["attributes[7291][0]"] == "31223"
    assert calls[-1].url.params["color_palettes[0]"] == "12"
    assert result.results[0].warnings
    assert len(calls) == 2
    bad = await catalog.search(
        SearchQuery(category_id="11", filters=FilterSelection(values={"brands": ["999999"]}))
    )
    assert bad.results[0].error.code == "invalid_filter" and len(calls) == 2


@pytest.mark.parametrize("values", [{"host": ["1"]}, {"brands": []}, {"brands": ["../1"]}])
def test_filter_parameter_injection_rejected(values):
    with pytest.raises(ValidationError):
        SearchQuery(category_id="11", filters=FilterSelection(values=values))


async def test_filter_category_mismatch(catalog_factory, payload):
    body = payload("digikala-filters")
    body["data"]["widgets"][0]["data"]["query_attributes"]["category_ids"] = [12]
    service = catalog_factory(lambda _: httpx.Response(200, json=body)).products
    result = await service.filters("11")
    assert result.error.code == "category_filter_not_applied"


async def test_chart_keeps_missing_dates_prices_and_variant_uncertainty(research):
    catalog, calls = research
    result = await catalog.products.history("1")
    assert result.error is None
    assert result.series[0].offer_id is None
    assert [p.date_text for p in result.series[0].points] == ["1405/07/01", "1405/07/03"]
    assert result.series[0].points[1].selling_price_rial is None
    assert result.series[0].points[0].seller_name == "A"
    assert result.series[0].points[1].is_marketable is False
    again = await catalog.products.history("1")
    assert again.observed_at == result.observed_at and len(calls) == 1


async def test_chart_rate_limit_and_schema_errors_are_not_cached(catalog_factory):
    responses = [httpx.Response(429), httpx.Response(200, json={"status": 200, "data": {}})]
    service = catalog_factory(lambda _: responses.pop(0)).products
    assert (await service.history("1")).error.code == "rate_limited"
    assert (await service.history("1")).error.code == "schema_changed"


async def test_research_tools_through_mcp(research):
    catalog, _ = research

    @asynccontextmanager
    async def factory():
        yield catalog

    async with Client(create_server(catalog_factory=factory)) as client:
        calls = {
            "get_category_filters": {"category_id": "11"},
            "get_products_batch": {"query": {"product_ids": ["1", "2", "3"]}},
            "compare_products": {"query": {"product_ids": ["1", "2"]}},
            "get_product_media": {"product_id": "1"},
            "get_product_price_history": {"product_id": "1"},
        }
        for tool, arguments in calls.items():
            result = await client.call_tool(tool, arguments)
            assert result.structured_content is not None
            assert not result.structured_content.get("error")
        invalid = await client.call_tool(
            "compare_products", {"query": {"product_ids": ["1"]}}, raise_on_error=False
        )
        assert invalid.is_error


async def test_reordered_filter_values_share_one_search_snapshot(research):
    catalog, calls = research
    first = SearchQuery(category_id="11", filters=FilterSelection(values={"brands": ["18", "10"]}))
    second = SearchQuery(
        category_id="11", filters=FilterSelection(values={"brands": ["10", "18", "18"]})
    )
    a, b = await catalog.search(first), await catalog.search(second)
    assert a.results[0].error is None and b.results[0].error is None
    assert len(calls) == 2  # Category metadata plus one search request.
