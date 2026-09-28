from typing import Literal, Self

from pydantic import Field, model_validator

from src.models.schemas.base import Model
from src.models.schemas.category import CategoryId
from src.models.schemas.common import Location, Money, QueryText
from src.models.schemas.market import Market, MarketError
from src.models.schemas.product import Product
from src.models.schemas.research import FilterSelection


class SearchQuery(Model):
    query: QueryText | None = None
    category_id: CategoryId | None = None
    page: int = Field(default=1, ge=1, le=500, strict=True)
    min_price_rial: Money | None = None
    max_price_rial: Money | None = None
    filters: FilterSelection = Field(default_factory=FilterSelection)
    sort: Literal["relevance", "price_asc", "price_desc"] = "relevance"

    @model_validator(mode="after")
    def valid_range(self) -> Self:
        if self.filters.values and self.category_id is None:
            raise ValueError("Category filters require category_id")
        if self.query is None and self.category_id is None:
            raise ValueError("Provide query or category_id")
        if (
            self.min_price_rial is not None
            and self.max_price_rial is not None
            and self.min_price_rial > self.max_price_rial
        ):
            raise ValueError("min_price_rial must not exceed max_price_rial")
        return self


class SearchResult(Model):
    market: Market
    category_id: CategoryId | None = None
    products: list[Product] = Field(default_factory=list)
    page: int
    total_pages: int | None = None
    total_items: int | None = None
    location: Location | None = None
    location_is_default: bool = False
    warnings: list[str] = Field(default_factory=list)
    error: MarketError | None = None


class SearchResponse(Model):
    results: list[SearchResult]
