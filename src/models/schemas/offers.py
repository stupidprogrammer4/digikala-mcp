"""Seller offers cached up to 60 seconds from a single Digikala product response."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from src.models.schemas.base import Model
from src.models.schemas.common import ProductId
from src.models.schemas.market import Market, MarketError
from src.models.schemas.product import Offer


class OfferListResult(Model):
    market: Market = Market.DIGIKALA
    product_id: ProductId
    variant_id: ProductId | None = None
    url: str | None = None
    observed_at: datetime | None = None
    coverage: Literal["product_response"] = "product_response"
    offers: list[Offer] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: MarketError | None = None
