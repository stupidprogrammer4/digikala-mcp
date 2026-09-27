import httpx
import pytest
from pydantic import ValidationError

from src.models import Market, SearchQuery


async def test_digikala_search_mapping_and_request(payload, catalog_factory):
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/discovery/api/v2/search"
        assert request.url.params["q"] == "هندزفری"
        assert request.url.params["page"] == "2"
        assert request.url.params["price[max]"] == "30000000"
        assert request.url.params["sort"] == "20"
        return httpx.Response(200, json=payload("digikala-search"))

    service = catalog_factory(handler)
    response = await service.search(
        SearchQuery(query="هندزفری", page=2, max_price_rial=30000000, sort="price_asc"),
        [Market.DIGIKALA],
    )
    result = response.results[0]
    assert result.error is None
    assert result.page == 2
    assert result.products
    product = next(p for p in result.products if p.product_id == "22672438")
    assert product.price_rial == 27999900
    assert product.offers[0].seller_id == "289366"
    assert product.offers[0].shipping_price_rial is None
    assert product.observed_at.tzinfo is not None


async def test_digikala_detail_identity_and_variants(payload, catalog_factory):
    service = catalog_factory(lambda r: httpx.Response(200, json=payload("digikala-product")))
    result = await service.get_product(Market.DIGIKALA, "22672438")
    assert result.error is None
    assert result.product.specifications
    assert len({o.offer_id for o in result.product.offers}) == len(result.product.offers)
    mismatch = await service.get_product(Market.DIGIKALA, "999")
    assert mismatch.error.code == "schema_changed"
    assert mismatch.product is None


async def test_search_timeout_is_sanitized(catalog_factory):
    def handler(request):
        raise httpx.ReadTimeout("private diagnostic", request=request)

    response = await catalog_factory(handler).search(SearchQuery(query="هندزفری"))
    assert len(response.results) == 1
    assert response.results[0].error.code == "timeout"
    assert response.results[0].error.retryable
    assert "private diagnostic" not in response.model_dump_json()


@pytest.mark.parametrize(
    "status,code",
    [(404, "not_found"), (429, "rate_limited"), (403, "access_denied"), (503, "upstream_error")],
)
async def test_http_errors(status, code, catalog_factory):
    service = catalog_factory(lambda r: httpx.Response(status, text="secret upstream body"))
    result = await service.get_product(Market.DIGIKALA, "1")
    assert result.error.code == code
    assert "secret" not in result.model_dump_json()


async def test_schema_drift_is_not_empty_success(catalog_factory):
    service = catalog_factory(lambda r: httpx.Response(200, json={"status": 200, "data": {}}))
    result = (await service.search(SearchQuery(query="x"), [Market.DIGIKALA])).results[0]
    assert result.error.code == "schema_changed"
    assert result.total_items is None


async def test_empty_search_is_valid(payload, catalog_factory):
    data = payload("digikala-search")
    listing = data["data"]["widgets"][0]["data"]
    listing["widgets"] = []
    listing["pager"] = {"total_pages": 0, "total_items": 0}
    service = catalog_factory(lambda r: httpx.Response(200, json=data))
    result = (await service.search(SearchQuery(query="nothing"), [Market.DIGIKALA])).results[0]
    assert result.error is None
    assert result.products == []
    assert result.total_items == 0


async def test_missing_price_is_unknown_and_not_in_budget(payload, catalog_factory):
    data = payload("digikala-search")
    for widget in data["data"]["widgets"][0]["data"]["widgets"]:
        widget["data"]["default_variant"]["price"] = {}
    service = catalog_factory(lambda r: httpx.Response(200, json=data))
    result = (await service.search(SearchQuery(query="x"), [Market.DIGIKALA])).results[0]
    assert all(p.price_rial is None for p in result.products)
    result = (
        await service.search(SearchQuery(query="x", max_price_rial=30000000), [Market.DIGIKALA])
    ).results[0]
    assert result.products == []
    assert result.warnings


async def test_autocomplete(payload, catalog_factory):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=payload("digikala-autocomplete"))

    service = catalog_factory(handler)
    dk = await service.autocomplete(Market.DIGIKALA, "هندزفری")
    assert dk.suggestions[0].text == "هندزفری بلوتوثی"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"query": " "},
        {"query": "x", "page": 0},
        {"query": "x", "page": True},
        {"query": "x", "min_price_rial": 20, "max_price_rial": 10},
        {"query": "x", "min_price_rial": 1.5},
        {"query": "x", "unexpected": True},
    ],
)
def test_invalid_queries(kwargs):
    with pytest.raises(ValidationError):
        SearchQuery(**kwargs)
