from datetime import datetime, timezone

from pydantic import Field

from src.models.schemas.base import Model
from src.models.schemas.common import Availability, Location, Money, ProductId
from src.models.schemas.market import Market, MarketError
from src.models.schemas.product_info import (
    MediaAsset,
    ProductRating,
    SellerRating,
    VariantAttribute,
)


class Offer(Model):
    variant_id: str | None = None
    offer_id: str | None = None
    seller_id: str | None = None
    seller_name: str | None = None
    price_rial: Money | None = None
    availability: Availability = "unknown"
    attributes: dict[str, str] = Field(default_factory=dict)
    warranty: str | None = None
    shipping_price_rial: Money | None = None
    variant_attributes: list[VariantAttribute] = Field(default_factory=list)
    seller_rating: SellerRating | None = None
    lead_time_days: int | None = Field(default=None, ge=0, strict=True)
    shipment_description: str | None = None
    order_limit: int | None = Field(default=None, ge=0, strict=True)


class Product(Model):
    market: Market
    product_id: ProductId
    title: str = Field(min_length=1)
    url: str
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    brand: str | None = None
    category_id: ProductId | None = None
    rating: ProductRating | None = None
    price_rial: Money | None = None
    availability: Availability = "unknown"
    offers: list[Offer] = Field(default_factory=list)
    media: list[MediaAsset] = Field(default_factory=list)
    specifications: dict[str, list[str]] = Field(default_factory=dict)
    is_ad: bool | None = None
    location: Location | None = None
    location_is_default: bool = False


class ProductResult(Model):
    market: Market
    product: Product | None = None
    error: MarketError | None = None
