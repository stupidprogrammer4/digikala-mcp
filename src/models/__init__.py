"""Public exports for the provider-neutral catalog contracts."""

from src.models.autocomplete import AutocompleteResult, Suggestion
from src.models.base import Model
from src.models.category import Category, CategoryId, CategoryListResult, CategoryQuery
from src.models.common import Availability, Location, Money, ProductId, QueryText
from src.models.comparison import (
    ComparisonItem,
    ComparisonRequest,
    ComparisonResponse,
    FieldDifference,
    OfferPairComparison,
    OfferSelection,
)
from src.models.market import Market, MarketError, MarketInfo, MarketList
from src.models.offers import OfferListResult
from src.models.product import Offer, Product, ProductResult
from src.models.search import SearchQuery, SearchResponse, SearchResult

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
