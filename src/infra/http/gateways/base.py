from abc import ABC, abstractmethod

from src.infra.http import GatewayError, HTTPConnection
from src.models.schemas import (
    AutocompleteResult,
    Category,
    Location,
    Market,
    Product,
    SearchQuery,
    SearchResult,
)
from src.models.schemas.product_content import (
    QuestionListResult,
    QuestionSort,
    RecommendationResult,
    RecommendationSections,
    ReviewListResult,
    ReviewSort,
    VariantListResult,
)
from src.models.schemas.research import CategoryFiltersResult, PriceHistoryResult
from src.models.schemas.trends import TrendSnapshot


class ABCMarketGateway(ABC):
    market: Market
    supports_autocomplete = False
    supports_categories = False
    supports_product_content = False
    supports_research = False

    def __init__(self, http: HTTPConnection):
        self.http = http

    async def trends(self) -> TrendSnapshot:
        raise GatewayError("capability_unknown", "Trend listing unavailable")

    async def list_categories(self) -> list[Category]:
        raise GatewayError(
            "capability_unknown", "Categories have not been verified for this market"
        )

    async def autocomplete(self, q: str) -> AutocompleteResult:
        raise GatewayError(
            "capability_unknown", "Autocomplete has not been verified for this market"
        )

    async def product_variants(self, product_id: str) -> VariantListResult:
        raise GatewayError("capability_unknown", "Product variants unavailable")

    async def product_reviews(
        self, product_id: str, page: int = 1, sort: ReviewSort = "default"
    ) -> ReviewListResult:
        raise GatewayError("capability_unknown", "Product reviews unavailable")

    async def product_questions(
        self, product_id: str, page: int = 1, sort: QuestionSort = "created_at"
    ) -> QuestionListResult:
        raise GatewayError("capability_unknown", "Product questions unavailable")

    async def recommendation_sections(self, product_id: str) -> RecommendationSections:
        raise GatewayError("capability_unknown", "Product recommendations unavailable")

    async def product_recommendations(
        self, product_id: str, section_key: str = "similar_products"
    ) -> RecommendationResult:
        raise GatewayError("capability_unknown", "Product recommendations unavailable")

    async def category_filters(self, category_id: str) -> CategoryFiltersResult:
        raise GatewayError("capability_unknown", "Category filters unavailable")

    async def product_price_history(self, product_id: str) -> PriceHistoryResult:
        raise GatewayError("capability_unknown", "Price history unavailable")

    @abstractmethod
    async def search(
        self, query: SearchQuery, location: Location | None = None
    ) -> SearchResult: ...

    @abstractmethod
    async def get_product(self, product_id: str, location: Location | None = None) -> Product: ...
