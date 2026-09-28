import copy

import httpx
import pytest
from pydantic import ValidationError

from src.models.schemas import CategoryQuery, Market, SearchQuery


async def test_category_tree_deduplicates_and_preserves_hierarchy(payload, catalog_factory):
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/v1/dictionaries/"
        assert dict(request.url.params) == {"types[0]": "category_tree", "hashes[0]": ""}
        return httpx.Response(200, json=payload("digikala-categories"))

    result = await catalog_factory(handler).list_categories()
    assert result.error is None
    assert result.total_items == 8
    assert result.total_pages == 1
    by_id = {c.category_id: c for c in result.categories}
    assert by_id["11"].title == "گوشی موبایل"
    assert by_id["11"].parent_id == "1"
    assert by_id["5966"].has_children
    assert not by_id["211"].has_children
    # Preserve an upstream parent even if it is absent from this tree.
    assert by_id["11545"].parent_id == "10647"


@pytest.mark.parametrize(
    "query, expected",
    [
        ({"roots_only": True}, ["1", "5966", "5967"]),
        ({"parent_id": "5966"}, ["3", "211"]),
        ({"query": "موبايل"}, ["1", "11"]),
        ({"query": "HEADPHONE"}, ["211"]),
        ({"query": "211"}, ["211"]),
        ({"query": "هدفون", "parent_id": "1"}, []),
        ({"query": "nonexistent"}, []),
        ({"page": 2, "page_size": 3}, ["18", "211", "5966"]),
        ({"page": 10}, []),
    ],
)
async def test_category_filters_and_pagination(query, expected, payload, catalog_factory):
    service = catalog_factory(lambda r: httpx.Response(200, json=payload("digikala-categories")))
    result = await service.list_categories(CategoryQuery(**query))
    assert result.error is None
    assert [c.category_id for c in result.categories] == expected
    if query.get("page") == 2:
        assert result.total_items == 8
        assert result.total_pages == 3


@pytest.mark.parametrize("status,code", [(429, "rate_limited"), (403, "access_denied")])
async def test_category_errors(status, code, catalog_factory):
    result = await catalog_factory(lambda r: httpx.Response(status)).list_categories()
    assert result.error.code == code
    assert result.total_items is None


@pytest.mark.parametrize("fault", ["missing", "wrong_type", "conflict", "bad_id"])
async def test_category_schema_drift(fault, payload, catalog_factory):
    body = payload("digikala-categories")
    rows = body["data"][0]["data"]["data"]
    if fault == "missing":
        body["data"] = []
    elif fault == "wrong_type":
        body["data"][0]["data"]["data"] = {}
    elif fault == "conflict":
        duplicate = copy.deepcopy(rows[0])
        duplicate["category"]["title_fa"] = "Conflicting name"
        rows.append(duplicate)
    else:
        rows[0]["category"]["id"] = True
    result = await catalog_factory(lambda r: httpx.Response(200, json=body)).list_categories()
    assert result.error.code == "schema_changed"
    assert result.total_items is None


@pytest.mark.parametrize("text", [None, "گوشی"])
async def test_category_search_sends_verified_filter(text, payload, catalog_factory):
    body = payload("digikala-search")
    body["data"]["widgets"][0]["data"]["query_attributes"] = {"category_ids": [211]}

    def handler(request):
        assert request.url.path == "/discovery/api/v2/search"
        assert request.url.params["categories[]"] == "211"
        assert "category_id" not in request.url.params
        assert request.url.params.get("q") == text
        assert request.url.params["page"] == "2"
        assert request.url.params["sort"] == "20"
        assert request.url.params["price[max]"] == "30000000"
        return httpx.Response(200, json=body)

    service = catalog_factory(handler)
    result = (
        await service.search(
            SearchQuery(
                query=text,
                category_id="211",
                page=2,
                sort="price_asc",
                max_price_rial=30000000,
            )
        )
    ).results[0]
    assert result.error is None
    assert result.category_id == "211"
    assert result.products


@pytest.mark.parametrize("applied", [[], [11], [211, 11]])
async def test_ignored_category_is_not_success(applied, payload, catalog_factory):
    body = payload("digikala-search")
    body["data"]["widgets"][0]["data"]["query_attributes"] = {"category_ids": applied}
    service = catalog_factory(lambda r: httpx.Response(200, json=body))
    result = (await service.search(SearchQuery(category_id="211"))).results[0]
    assert result.error.code == "category_filter_not_applied"
    assert result.category_id == "211"
    assert result.products == []


async def test_missing_category_confirmation_is_schema_error(payload, catalog_factory):
    service = catalog_factory(lambda r: httpx.Response(200, json=payload("digikala-search")))
    result = (await service.search(SearchQuery(category_id="211"))).results[0]
    assert result.error.code == "schema_changed"


async def test_autocomplete_only_exposes_category_ids(payload, catalog_factory):
    body = payload("digikala-autocomplete")
    body["data"]["auto_complete"].append({"type": "keyword", "id": 99, "title": "example"})
    service = catalog_factory(lambda r: httpx.Response(200, json=body))
    result = await service.autocomplete(Market.DIGIKALA, "هندزفری")
    assert result.suggestions[0].category_id == "211"
    assert result.suggestions[-1].category_id is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"category_id": "../11"},
        {"category_id": True},
        {"category_id": "0"},
        {"category_id": "11", "query": " "},
    ],
)
def test_invalid_category_search(kwargs):
    with pytest.raises(ValidationError):
        SearchQuery(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"roots_only": True, "parent_id": "11"},
        {"page_size": 101},
        {"page": 0},
        {"parent_id": "../11"},
        {"query": " "},
        {"page_size": True},
    ],
)
def test_invalid_category_query(kwargs):
    with pytest.raises(ValidationError):
        CategoryQuery(**kwargs)
