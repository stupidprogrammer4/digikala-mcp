"""MCP tool definitions; delegate catalog operations to the application layer."""

from fastmcp import FastMCP
from fastmcp.dependencies import Depends
from mcp_types import ToolAnnotations

from src.app.catalog import CatalogService
from src.models.schemas import (
    AutocompleteResult,
    CategoryListResult,
    CategoryQuery,
    Location,
    Market,
    MarketList,
    OfferListResult,
    ProductId,
    ProductResult,
    QueryText,
    SearchQuery,
    SearchResponse,
)
from src.models.schemas.trends import TrendSnapshot
from src.tools.dependencies import from_dishka


def register_catalog_tools(server: FastMCP) -> None:
    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=True)

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
    async def list_markets(
        service: CatalogService = Depends(from_dishka(CatalogService)),
    ) -> MarketList:
        """List implemented markets and verification status; this is not a live health probe."""
        return service.list_markets()

    @server.tool(annotations=read_only)
    async def get_trend_snapshot(
        service: CatalogService = Depends(from_dishka(CatalogService)),
    ) -> TrendSnapshot:
        """Read the current homepage best-selling listing, preserving upstream order.

        This is a timestamped popularity signal, not a historical trend series.
        Sales counts, period and category-specific popularity are not supplied.
        """
        result = await service.trends()
        return result

    @server.tool(annotations=read_only)
    async def list_categories(
        service: CatalogService = Depends(from_dishka(CatalogService)),
        query: CategoryQuery | None = None,
    ) -> CategoryListResult:
        """Find live Digikala category IDs by Persian/English name, code, or ID substring.

        Omit query to page through all categories. roots_only lists top-level categories;
        parent_id lists direct children. Text and parent filters can be combined.
        Pagination is local over the current upstream tree, ordered by numeric ID.
        Use category_id in search_products, with or without search text.
        """
        return await service.list_categories(query)

    @server.tool(annotations=read_only)
    async def search_products(
        query: SearchQuery,
        service: CatalogService = Depends(from_dishka(CatalogService)),
        markets: list[Market] | None = None,
        location: Location | None = None,
    ) -> SearchResponse:
        """Search one native page per market; all configured markets are selected by default.

        Budgets are IRR, not toman.
        Get category_id from list_categories or autocomplete to restrict the search.
        Provide query text, category_id, or both; category alone browses that category.
        Sorting is per market/page, not a global ranking. Page sizes and totals are upstream.
        Errors are isolated per market. Location is unused by the current Digikala adapter.
        """
        return await service.search(query, markets, location)

    @server.tool(annotations=read_only)
    async def get_product(
        market: Market,
        product_id: ProductId,
        service: CatalogService = Depends(from_dishka(CatalogService)),
        location: Location | None = None,
    ) -> ProductResult:
        """Read product details and seller offers, cached up to 60 seconds.

        Shipping remains unknown. Location is unused by the current Digikala adapter.
        """
        return await service.get_product(market, product_id, location)

    @server.tool(annotations=read_only)
    async def list_offers(
        product_id: ProductId,
        service: CatalogService = Depends(from_dishka(CatalogService)),
        variant_id: ProductId | None = None,
    ) -> OfferListResult:
        """Read seller offers cached up to 60 seconds, optionally matching an exact variant_id.

        IDs come from get_product/search results. Never substitute a seller or similar variant.
        Coverage is limited to offers in the current product response, not all possible sellers.
        Prices are integer rials; unknown prices and availability stay unknown. Out-of-stock
        offers are retained. observed_at is the product observation time, not a stock guarantee.
        No account login, cart mutation, or separate seller API is used.
        """
        return await service.list_offers(product_id, variant_id)

    @server.tool(annotations=read_only)
    async def autocomplete(
        market: Market,
        query: QueryText,
        service: CatalogService = Depends(from_dishka(CatalogService)),
    ) -> AutocompleteResult:
        """Suggest search phrases. Currently verified only for Digikala."""
        return await service.autocomplete(market, query)
