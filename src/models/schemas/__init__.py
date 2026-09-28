"""Public MCP input and output contracts."""

from src.models.schemas.autocomplete import AutocompleteResult, Suggestion
from src.models.schemas.base import Model
from src.models.schemas.category import Category, CategoryId, CategoryListResult, CategoryQuery
from src.models.schemas.common import Availability, Location, Money, ProductId, QueryText
from src.models.schemas.comparison import (
    ComparisonItem,
    ComparisonRequest,
    ComparisonResponse,
    FieldDifference,
    OfferPairComparison,
    OfferSelection,
)
from src.models.schemas.market import Market, MarketError, MarketInfo, MarketList
from src.models.schemas.offers import OfferListResult
from src.models.schemas.product import Offer, Product, ProductResult
from src.models.schemas.search import SearchQuery, SearchResponse, SearchResult

__all__ = [
    "AutocompleteResult",
    "Availability",
    "Category",
    "CategoryId",
    "CategoryListResult",
    "CategoryQuery",
    "ComparisonItem",
    "ComparisonRequest",
    "ComparisonResponse",
    "FieldDifference",
    "Location",
    "Market",
    "MarketError",
    "MarketInfo",
    "MarketList",
    "Model",
    "Money",
    "Offer",
    "OfferListResult",
    "OfferPairComparison",
    "OfferSelection",
    "Product",
    "ProductId",
    "ProductResult",
    "QueryText",
    "SearchQuery",
    "SearchResponse",
    "SearchResult",
    "Suggestion",
]
