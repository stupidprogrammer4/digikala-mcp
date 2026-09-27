"""Inputs and fresh observations for comparing explicitly selected seller offers."""

from datetime import datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from src.models.base import Model
from src.models.common import Location, Money, ProductId
from src.models.market import Market, MarketError
from src.models.product import Offer


class OfferSelection(Model):
    market: Market
    product_id: ProductId
    offer_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    expected_price_rial: Money | None = None


class ComparisonRequest(Model):
    selections: list[OfferSelection] = Field(min_length=2, max_length=6)
    location: Location | None = None

    @model_validator(mode="after")
    def distinct_selections(self) -> Self:
        keys = {(s.market, s.product_id, s.offer_id) for s in self.selections}
        if len(keys) != len(self.selections):
            raise ValueError("Select distinct offers; the same offer cannot be compared to itself")
        return self


class ComparisonItem(Model):
    selection: OfferSelection
    title: str | None = None
    url: str | None = None
    brand: str | None = None
    specifications: dict[str, list[str]] = Field(default_factory=dict)
    offer: Offer | None = None
    observed_at: datetime | None = None
    location: Location | None = None
    location_is_default: bool = False
    price_changed: bool | None = None
    error: MarketError | None = None


class FieldDifference(Model):
    field: str
    left: list[str] | None
    right: list[str] | None


class OfferPairComparison(Model):
    left_index: int
    right_index: int
    status: Literal["compared", "incomplete"]
    identity: Literal["same_listing", "unverified"] = "unverified"
    attributes: Literal["match", "different", "unknown"] = "unknown"
    warranty: Literal["match", "different", "unknown"] = "unknown"
    price_difference_rial: int | None = Field(
        default=None, description="Right offer's item price minus left offer's; excludes shipping."
    )
    differences: list[FieldDifference] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ComparisonResponse(Model):
    items: list[ComparisonItem]
    pairs: list[OfferPairComparison]
    notes: list[str] = Field(
        default_factory=lambda: [
            "Item prices exclude shipping; no cheapest checkout or product ranking is implied.",
            "Matching titles or attributes do not establish identity across listings.",
        ]
    )
