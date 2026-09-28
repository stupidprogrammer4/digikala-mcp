from fastmcp import FastMCP
from fastmcp.dependencies import Depends
from mcp_types import ToolAnnotations

from src.app.catalog import CatalogService
from src.models.schemas.comparison import ComparisonRequest, ComparisonResponse
from src.tools.dependencies import from_dishka


def register_comparison_tools(server: FastMCP) -> None:
    @server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True))
    async def compare_offers(
        request: ComparisonRequest, service: CatalogService = Depends(from_dishka(CatalogService))
    ) -> ComparisonResponse:
        """Compare 2-6 explicit seller offers from get_product results.

        Catalog observations are cached for up to 60 seconds.
        Use each offer's offer_id, not variant_id or seller_id. No substitute is chosen if it
        disappears. expected_price_rial optionally detects price changes. All money is IRR.
        Differences are right minus left item prices, excluding shipping. Pair indexes are
        zero-based and refer to input order. Matching names/attributes do not prove equivalent
        products across listings. Item errors preserve other results.
        """
        return await service.compare_offers(request)
