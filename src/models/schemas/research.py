"""Bounded catalog research contracts; missing observations stay unknown."""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from src.models.schemas.base import Model
from src.models.schemas.common import Money, ProductId
from src.models.schemas.market import MarketError
from src.models.schemas.product import ProductResult
from src.models.schemas.product_content import ProductObservation
from src.models.schemas.product_info import MediaAsset


class ProductBatchQuery(Model):
    product_ids: list[ProductId] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def distinct(self) -> Self:
        if len(set(self.product_ids)) != len(self.product_ids):
            raise ValueError("Select distinct product IDs")
        return self


class ProductComparisonQuery(ProductBatchQuery):
    product_ids: list[ProductId] = Field(min_length=2, max_length=6)


class ProductBatchResult(Model):
    results: dict[str, ProductResult]
    cache_ttl_seconds: Literal[60] = 60


class SpecificationComparison(Model):
    title: str
    values: dict[str, list[str] | None]
    relation: Literal["same", "different", "unknown"]


class ProductComparisonResult(ProductBatchResult):
    specifications: list[SpecificationComparison]
    warnings: list[str] = Field(
        default_factory=lambda: [
            "Specification labels and values are compared literally; missing values are unknown. "
            "Different category schemas and units are not inferred to be equivalent."
        ]
    )


class ProductMediaResult(ProductObservation):
    media: list[MediaAsset] = Field(default_factory=list)


class PriceHistoryPoint(Model):
    date_text: str
    selling_price_rial: Money | None = None
    original_price_rial: Money | None = None
    is_marketable: bool | None = None
    seller_name: str | None = None
    warranty: str | None = None


class PriceHistorySeries(Model):
    title: str
    points: list[PriceHistoryPoint]
    # The chart does not reliably identify the exact variant or seller.
    offer_id: ProductId | None = None


class PriceHistoryResult(ProductObservation):
    selection_title: str | None = None
    series: list[PriceHistorySeries] = Field(default_factory=list)


FilterKey = Annotated[str, Field(pattern=r"^(brands|color_palettes|attribute_[1-9][0-9]*)$")]
FilterValues = Annotated[list[ProductId], Field(min_length=1, max_length=20)]


class FilterSelection(Model):
    values: dict[FilterKey, FilterValues] = Field(default_factory=dict, max_length=10)

    @model_validator(mode="after")
    def normalize(self) -> Self:
        self.values = {key: sorted(set(values)) for key, values in self.values.items()}
        return self


class FilterOption(Model):
    value_id: ProductId
    title: str
    title_en: str | None = None


class CategoryFilter(Model):
    key: FilterKey
    title: str
    options: list[FilterOption]


class CategoryFiltersResult(Model):
    category_id: ProductId
    filters: list[CategoryFilter] = Field(default_factory=list)
    min_price_rial: Money | None = None
    max_price_rial: Money | None = None
    source_url: str | None = None
    observed_at: datetime | None = None
    cache_ttl_seconds: Literal[60] = 60
    warnings: list[str] = Field(default_factory=list)
    error: MarketError | None = None
