import sys
from contextlib import asynccontextmanager

import httpx
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from src.server import create_server


async def test_all_tools_through_mcp_client(payload, comparison_payload, catalog_factory):
    def handler(request):
        if request.url.path == "/v1/dictionaries/":
            name = "digikala-categories"
        elif request.url.path.endswith("autocomplete"):
            name = "digikala-autocomplete"
        elif request.url.path.startswith("/product/v1"):
            return httpx.Response(200, json=comparison_payload())
        else:
            name = "digikala-search"
            if "categories[]" in request.url.params:
                body = payload(name)
                body["data"]["widgets"][0]["data"]["query_attributes"] = {"category_ids": [211]}
                return httpx.Response(200, json=body)
        return httpx.Response(200, json=payload(name))

    service = catalog_factory(handler)
    lifecycle = []

    @asynccontextmanager
    async def provide_catalog():
        lifecycle.append("started")
        try:
            yield service
        finally:
            lifecycle.append("closed")

    server = create_server(catalog_factory=provide_catalog)
    assert lifecycle == []
    async with Client(server) as client:
        assert lifecycle == ["started"]
        tools = await client.list_tools()
        assert {t.name for t in tools} == {
            "get_category_filters",
            "get_products_batch",
            "compare_products",
            "get_product_media",
            "get_product_price_history",
            "prepare_account_cart_change",
            "add_to_account_cart",
            "update_account_cart_item",
            "remove_from_account_cart",
            "get_account_cart_operation",
            "reconcile_account_cart_operation",
            "get_cart_operation",
            "reconcile_cart_operation",
            "login_account",
            "logout_account",
            "read_account_cart",
            "replace_account_cart",
            "get_trend_snapshot",
            "get_product_variants",
            "get_product_variant_types",
            "get_product_reviews",
            "get_product_questions",
            "get_product_ratings",
            "list_product_sellers",
            "compare_product_sellers",
            "list_product_recommendation_sections",
            "get_product_recommendations",
            "list_markets",
            "list_categories",
            "search_products",
            "get_product",
            "list_offers",
            "autocomplete",
            "compare_offers",
            "read_cart",
            "get_cart_limits",
            "prepare_cart_change",
            "add_to_cart",
            "update_cart_item",
            "remove_from_cart",
        }
        write_tools = {
            "prepare_account_cart_change",
            "add_to_account_cart",
            "update_account_cart_item",
            "remove_from_account_cart",
            "reconcile_account_cart_operation",
            "reconcile_cart_operation",
            "prepare_cart_change",
            "add_to_cart",
            "update_cart_item",
            "remove_from_cart",
            "login_account",
            "logout_account",
            "replace_account_cart",
        }
        assert all(
            t.annotations is not None
            and t.annotations.read_only_hint == (t.name not in write_tools)
            for t in tools
        )
        assert all(t.output_schema for t in tools)
        markets = await client.call_tool("list_markets", raise_on_error=False)
        assert not markets.is_error
        assert markets.structured_content is not None
        assert [m["market"] for m in markets.structured_content["markets"]] == ["digikala"]
        categories = await client.call_tool(
            "list_categories", {"query": {"query": "headphone"}}, raise_on_error=False
        )
        assert not categories.is_error
        assert categories.structured_content is not None
        assert categories.structured_content["categories"][0]["category_id"] == "211"
        all_categories = await client.call_tool("list_categories", raise_on_error=False)
        assert all_categories.structured_content is not None
        assert all_categories.structured_content["total_items"] == 8
        category_search = await client.call_tool(
            "search_products", {"query": {"category_id": "211"}}, raise_on_error=False
        )
        assert not category_search.is_error
        assert category_search.structured_content is not None
        assert category_search.structured_content["results"][0]["category_id"] == "211"
        assert category_search.structured_content is not None
        assert category_search.structured_content["results"][0]["error"] is None
        search = await client.call_tool(
            "search_products", {"query": {"query": "هندزفری"}}, raise_on_error=False
        )
        assert not search.is_error
        assert search.structured_content is not None
        assert all(r["products"] for r in search.structured_content["results"])
        product = await client.call_tool(
            "get_product", {"market": "digikala", "product_id": "22672438"}, raise_on_error=False
        )
        assert product.structured_content is not None
        assert product.structured_content["product"]["product_id"] == "22672438"
        offers = await client.call_tool(
            "list_offers",
            {"product_id": "22672438", "variant_id": "84016751"},
            raise_on_error=False,
        )
        assert not offers.is_error
        assert offers.structured_content is not None
        assert offers.structured_content["error"] is None
        assert offers.structured_content is not None
        assert offers.structured_content["offers"][0]["offer_id"] == "84016751"
        invalid_offers = await client.call_tool(
            "list_offers",
            {"product_id": "22672438", "variant_id": "../private"},
            raise_on_error=False,
        )
        assert invalid_offers.is_error
        completion = await client.call_tool(
            "autocomplete", {"market": "digikala", "query": "هند"}, raise_on_error=False
        )
        assert completion.structured_content is not None
        assert completion.structured_content["suggestions"]
        selections = [
            {"market": "digikala", "product_id": "22672438", "offer_id": "84016750"},
            {"market": "digikala", "product_id": "22672438", "offer_id": "84016751"},
        ]
        comparison = await client.call_tool(
            "compare_offers", {"request": {"selections": selections}}, raise_on_error=False
        )
        assert not comparison.is_error
        assert comparison.structured_content is not None
        compared = comparison.structured_content
        assert all(item["offer"] and item["error"] is None for item in compared["items"])
        assert compared["pairs"][0]["identity"] == "same_listing"
        assert compared["pairs"][0]["price_difference_rial"] == 50000
        removed_market = await client.call_tool(
            "get_product", {"market": "snappshop", "product_id": "1327041642"}, raise_on_error=False
        )
        assert removed_market.is_error
        removed_search = await client.call_tool(
            "search_products",
            {"query": {"query": "x"}, "markets": ["snappshop"]},
            raise_on_error=False,
        )
        assert removed_search.is_error
        bad_comparison = await client.call_tool(
            "compare_offers",
            {"request": {"selections": [selections[0], selections[0]]}},
            raise_on_error=False,
        )
        assert bad_comparison.is_error
        bad = await client.call_tool(
            "get_product",
            {"market": "digikala", "product_id": "../../private"},
            raise_on_error=False,
        )
        assert bad.is_error
        bad = await client.call_tool(
            "search_products",
            {"query": {"query": "x", "min_price_rial": 20, "max_price_rial": 10}},
            raise_on_error=False,
        )
        assert bad.is_error

    assert lifecycle == ["started", "closed"]


async def test_stdio_entrypoint_without_network():
    async with Client(StdioTransport(command=sys.executable, args=["-m", "src.server"])) as client:
        result = await client.call_tool("list_markets", raise_on_error=False)
        assert not result.is_error
        assert result.structured_content is not None
        assert [m["market"] for m in result.structured_content["markets"]] == ["digikala"]
