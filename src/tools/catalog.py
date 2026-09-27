"""MCP tool definitions; delegate catalog operations to the application layer."""

from mcp.server import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.types import ToolAnnotations

from src.app.services import Services
from src.models import (
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


def register_catalog_tools(server: MCPServer[Services]) -> None:
    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=True)

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
    async def list_markets(ctx: Context[Services]) -> MarketList:
        """List implemented markets and verification status; this is not a live health probe."""
        return ctx.request_context.lifespan_context.catalog.list_markets()

    @server.tool(annotations=read_only)
    async def list_categories(
        ctx: Context[Services],
        query: CategoryQuery | None = None,
    ) -> CategoryListResult:
        """Find live Digikala category IDs by Persian/English name, code, or ID substring.

        Omit query to page through all categories. roots_only lists top-level categories;
        parent_id lists direct children. Text and parent filters can be combined.
        Pagination is local over the current upstream tree, ordered by numeric ID.
        Use category_id in search_products, with or without search text.
        """
        return await ctx.request_context.lifespan_context.catalog.list_categories(query)

    @server.tool(annotations=read_only)
    async def search_products(
        query: SearchQuery,
        ctx: Context[Services],
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
        return await ctx.request_context.lifespan_context.catalog.search(query, markets, location)

    @server.tool(annotations=read_only)
    async def get_product(
        market: Market,
        product_id: ProductId,
        ctx: Context[Services],
        location: Location | None = None,
    ) -> ProductResult:
        """Read fresh product details and variant/seller offers using a search result's product_id.

        Shipping remains unknown. Location is unused by the current Digikala adapter.
        """
        return await ctx.request_context.lifespan_context.catalog.get_product(
            market, product_id, location
        )

    @server.tool(annotations=read_only)
    async def list_offers(
        product_id: ProductId,
        ctx: Context[Services],
        variant_id: ProductId | None = None,
    ) -> OfferListResult:
        """Read fresh seller offers for a Digikala product, optionally matching an exact variant_id.

        IDs come from get_product/search results. Never substitute a seller or similar variant.
        Coverage is limited to offers in the current product response, not all possible sellers.
        Prices are integer rials; unknown prices and availability stay unknown. Out-of-stock
        offers are retained. observed_at is the product observation time, not a stock guarantee.
        No account login, cart mutation, or separate seller API is used.
        """
        return await ctx.request_context.lifespan_context.catalog.list_offers(
            product_id, variant_id
        )

    @server.tool(annotations=read_only)
    async def autocomplete(
        market: Market, query: QueryText, ctx: Context[Services]
    ) -> AutocompleteResult:
        """Suggest search phrases. Currently verified only for Digikala."""
        return await ctx.request_context.lifespan_context.catalog.autocomplete(market, query)
