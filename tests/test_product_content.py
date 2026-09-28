"""Product endpoints use sanitized public fixtures; altered edge cases are synthetic."""

import copy
from contextlib import asynccontextmanager

import httpx
import pytest
from fastmcp import Client
from pydantic import ValidationError

from src.models.schemas.product_content import SellerComparisonQuery
from src.server import create_server


@pytest.fixture
def product_catalog(payload, catalog_factory):
    calls = []
    paths = {
        "/product/v1/products/22672438/": "digikala-product-content",
        "/v1/product/22672438/variants/": "digikala-variants",
        "/v1/rate-review/products/22672438/": "digikala-reviews",
        "/v1/product/22672438/questions/": "digikala-questions",
        "/product/v1/recommendations/22672438/section/": "digikala-recommendation-sections",
        "/product/v1/recommendations/22672438/items/": "digikala-recommendations",
    }

    def handle(request):
        calls.append(request)
        assert request.method == "GET"
        assert "cookie" not in request.headers and "authorization" not in request.headers
        body = payload(paths[request.url.path])
        if request.url.path.endswith("/variants/"):
            # A second seller and a second color; no equivalence based on title alone.
            second = copy.deepcopy(body["data"][0])
            second["id"] = 9991
            second["seller"]["id"] = 999
            second["price"]["selling_price"] += 1000
            second["themes"][0]["value"].update(id=1, title="مشکی")
            body["data"].append(second)
        if request.url.params.get("page"):
            body["data"]["pager"]["current_page"] = int(request.url.params["page"])
        return httpx.Response(200, json=body)

    return catalog_factory(handle), calls


async def test_variant_types_sellers_and_comparison_share_snapshot(product_catalog):
    catalog, calls = product_catalog
    service = catalog.products
    variants = await service.variants("22672438")
    types = await service.variant_types("22672438")
    sellers = await service.sellers("22672438")
    assert len(calls) == 1
    assert variants.error is None and len(variants.variants) == 2
    assert types.types[0].nature == "color"
    assert {v.value_id for v in types.types[0].values} == {"1", "2"}
    assert len(sellers.sellers) == 2
    assert sellers.sellers[0].rating.satisfaction_percent == 74
    assert sellers.observed_at == variants.observed_at == types.observed_at
    comparison = await service.compare_sellers(
        SellerComparisonQuery(product_id="22672438", offer_ids=["84016750", "9991"])
    )
    assert len(calls) == 2
    assert comparison.pairs[0].price_difference_rial == 1000
    assert comparison.pairs[0].attributes == "different"
    assert comparison.items[0].observed_at == variants.observed_at
    assert comparison.items[0].offer.shipping_price_rial is None


async def test_reviews_questions_ratings_preserve_scales_and_page_keys(product_catalog):
    catalog, calls = product_catalog
    service = catalog.products
    reviews = await service.reviews("22672438")
    assert reviews.error is None and reviews.reviews[0].rating == 5
    assert reviews.reviews[0].is_buyer is True
    assert reviews.pager.total_items == 6
    assert "user_id" not in reviews.model_dump_json()
    assert "order_item_id" not in reviews.model_dump_json()
    questions = await service.questions("22672438")
    assert questions.error is None and questions.pager.total_items == 3
    assert any(q.answers for q in questions.questions)
    assert "sender" not in questions.model_dump_json()
    ratings = await service.ratings("22672438")
    assert ratings.rating.score_percent == 100
    assert ratings.rating.distribution_percent["100"] == 100
    await service.reviews("22672438")
    assert len(calls) == 3
    await service.reviews("22672438", 2, "newest")
    assert len(calls) == 4
    assert calls[-1].url.params["page"] == "2"
    assert calls[-1].url.params["sort"] == "newest"


async def test_recommendations_use_observed_sections_and_local_limit(product_catalog):
    catalog, calls = product_catalog
    service = catalog.products
    sections = await service.recommendation_sections("22672438")
    assert sections.sections[0].key == "similar_products"
    first = await service.recommendations("22672438", limit=1)
    second = await service.recommendations("22672438", limit=3)
    assert first.error is None
    assert len(first.products) == 1 and len(second.products) == 3
    assert first.source_count == second.source_count == 3
    assert first.observed_at == second.observed_at
    assert len(calls) == 3
    assert calls[-1].url.params["categoryId"] == "211"
    invalid = await service.recommendations("22672438", "nonexistent")
    assert invalid.error.code == "section_not_found" and len(calls) == 3


@pytest.mark.parametrize(
    "operation",
    ["variants", "reviews", "questions", "ratings", "recommendations", "recommendation_sections"],
)
async def test_errors_are_explicit_and_not_cached(catalog_factory, operation):
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        return httpx.Response(429, text="private upstream body")

    service = catalog_factory(handle).products
    for _ in range(2):
        result = await getattr(service, operation)("1")
        assert result.error.code == "rate_limited" and result.observed_at is None
        assert "private upstream body" not in result.model_dump_json()
    assert calls == 2


@pytest.mark.parametrize(
    "operation,fixture,key",
    [
        ("variants", "digikala-variants", None),
        ("reviews", "digikala-reviews", "comments"),
        ("questions", "digikala-questions", "questions"),
    ],
)
async def test_missing_or_duplicate_schema_is_not_success(
    payload, catalog_factory, operation, fixture, key
):
    data = payload(fixture)
    rows = data["data"] if key is None else data["data"][key]
    rows.append(copy.deepcopy(rows[0]))
    service = catalog_factory(lambda _: httpx.Response(200, json=data)).products
    result = await getattr(service, operation)("22672438")
    assert result.error.code == "schema_changed"


async def test_product_tools_over_mcp(product_catalog):
    catalog, _ = product_catalog

    @asynccontextmanager
    async def factory():
        yield catalog

    async with Client(create_server(catalog_factory=factory)) as client:
        for tool in [
            "get_product_variants",
            "get_product_variant_types",
            "list_product_sellers",
            "get_product_reviews",
            "get_product_questions",
            "get_product_ratings",
            "list_product_recommendation_sections",
            "get_product_recommendations",
        ]:
            result = await client.call_tool(tool, {"product_id": "22672438"}, raise_on_error=False)
            assert not result.is_error, tool
            assert result.structured_content is not None
            assert result.structured_content["error"] is None, tool
        compared = await client.call_tool(
            "compare_product_sellers",
            {"query": {"product_id": "22672438", "offer_ids": ["84016750", "9991"]}},
            raise_on_error=False,
        )
        assert not compared.is_error
        assert compared.structured_content is not None
        assert compared.structured_content["pairs"][0]["price_difference_rial"] == 1000
        for args in [
            {"product_id": "../private"},
            {"product_id": "1", "page": 0},
            {"product_id": "1", "sort": "invented"},
        ]:
            assert (
                await client.call_tool("get_product_reviews", args, raise_on_error=False)
            ).is_error


async def test_invalid_ids_never_reach_upstream(catalog_factory):
    def handle(request):
        pytest.fail("Invalid IDs must not reach the network")

    service = catalog_factory(handle).products
    with pytest.raises(ValidationError):
        await service.variants("../private")
    with pytest.raises(ValidationError):
        await service.questions("1", 0)
    with pytest.raises(ValidationError):
        SellerComparisonQuery(product_id="1", offer_ids=["2", "2"])


async def test_recommendation_error_envelope_does_not_look_like_empty_success(
    payload, catalog_factory
):
    def handle(request):
        if request.url.path.startswith("/product/v1/products/"):
            return httpx.Response(200, json=payload("digikala-product-content"))
        return httpx.Response(200, json={"status": 500, "data": {"sections": []}})

    result = await catalog_factory(handle).products.recommendation_sections("22672438")
    assert result.error is not None and result.error.code == "upstream_error"
    assert result.observed_at is None


async def test_empty_question_page_is_distinct_from_missing_schema(payload, catalog_factory):
    data = payload("digikala-questions")
    data["data"]["questions"] = []
    service = catalog_factory(lambda _: httpx.Response(200, json=data)).products
    result = await service.questions("22672438")
    assert result.error is None and result.questions == [] and result.observed_at is not None
    del data["data"]["questions"]
    service = catalog_factory(lambda _: httpx.Response(200, json=data)).products
    result = await service.questions("22672438")
    assert result.error is not None and result.error.code == "schema_changed"
