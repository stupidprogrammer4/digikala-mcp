import sys
from contextlib import asynccontextmanager

import httpx
from mcp import Client, StdioServerParameters

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
        tools = (await client.list_tools()).tools
        assert {t.name for t in tools} == {
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
        write_tools = {"prepare_cart_change", "add_to_cart", "update_cart_item", "remove_from_cart"}
        assert all(
            t.annotations is not None
            and t.annotations.read_only_hint == (t.name not in write_tools)
            for t in tools
        )
        assert all(t.output_schema for t in tools)
        markets = await client.call_tool("list_markets")
        assert not markets.is_error
        assert [m["market"] for m in markets.structured_content["markets"]] == ["digikala"]
        categories = await client.call_tool("list_categories", {"query": {"query": "headphone"}})
        assert not categories.is_error
        assert categories.structured_content["categories"][0]["category_id"] == "211"
        all_categories = await client.call_tool("list_categories")
        assert all_categories.structured_content["total_items"] == 8
        category_search = await client.call_tool(
            "search_products", {"query": {"category_id": "211"}}
        )
        assert not category_search.is_error
        assert category_search.structured_content["results"][0]["category_id"] == "211"
        assert category_search.structured_content["results"][0]["error"] is None
        search = await client.call_tool("search_products", {"query": {"query": "هندزفری"}})
        assert not search.is_error
        assert all(r["products"] for r in search.structured_content["results"])
        product = await client.call_tool(
            "get_product", {"market": "digikala", "product_id": "22672438"}
        )
        assert product.structured_content["product"]["product_id"] == "22672438"
        offers = await client.call_tool(
            "list_offers", {"product_id": "22672438", "variant_id": "84016751"}
        )
        assert not offers.is_error
        assert offers.structured_content["error"] is None
        assert offers.structured_content["offers"][0]["offer_id"] == "84016751"
        invalid_offers = await client.call_tool(
            "list_offers", {"product_id": "22672438", "variant_id": "../private"}
        )
        assert invalid_offers.is_error
        completion = await client.call_tool("autocomplete", {"market": "digikala", "query": "هند"})
        assert completion.structured_content["suggestions"]
        selections = [
            {"market": "digikala", "product_id": "22672438", "offer_id": "84016750"},
            {"market": "digikala", "product_id": "22672438", "offer_id": "84016751"},
        ]
        comparison = await client.call_tool(
            "compare_offers", {"request": {"selections": selections}}
        )
        assert not comparison.is_error
        compared = comparison.structured_content
        assert all(item["offer"] and item["error"] is None for item in compared["items"])
        assert compared["pairs"][0]["identity"] == "same_listing"
        assert compared["pairs"][0]["price_difference_rial"] == 50000
        removed_market = await client.call_tool(
            "get_product", {"market": "snappshop", "product_id": "1327041642"}
        )
        assert removed_market.is_error
        removed_search = await client.call_tool(
            "search_products", {"query": {"query": "x"}, "markets": ["snappshop"]}
        )
        assert removed_search.is_error
        bad_comparison = await client.call_tool(
            "compare_offers", {"request": {"selections": [selections[0], selections[0]]}}
        )
        assert bad_comparison.is_error
        bad = await client.call_tool(
            "get_product", {"market": "digikala", "product_id": "../../private"}
        )
        assert bad.is_error
        bad = await client.call_tool(
            "search_products", {"query": {"query": "x", "min_price_rial": 20, "max_price_rial": 10}}
        )
        assert bad.is_error

    assert lifecycle == ["started", "closed"]


async def test_stdio_entrypoint_without_network():
    async with Client(
        StdioServerParameters(command=sys.executable, args=["-m", "src.server"])
    ) as client:
        result = await client.call_tool("list_markets")
        assert not result.is_error
        assert [m["market"] for m in result.structured_content["markets"]] == ["digikala"]
