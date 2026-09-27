from mcp.server import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.types import ToolAnnotations

from src.app.services import Services
from src.models.comparison import ComparisonRequest, ComparisonResponse


def register_comparison_tools(server: MCPServer[Services]) -> None:
    @server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True))
    async def compare_offers(
        request: ComparisonRequest, ctx: Context[Services]
    ) -> ComparisonResponse:
        """Refresh and compare 2-6 explicit seller offers from get_product results.

        Use each offer's offer_id, not variant_id or seller_id. No substitute is chosen if it
        disappears. expected_price_rial optionally detects price changes. All money is IRR.
        Differences are right minus left item prices, excluding shipping. Pair indexes are
        zero-based and refer to input order. Matching names/attributes do not prove equivalent
        products across listings. Item errors preserve other results.
        """
        return await ctx.request_context.lifespan_context.catalog.compare_offers(request)
