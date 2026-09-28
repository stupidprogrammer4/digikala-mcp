"""Cart contracts. Money is integer rial; item limits count units, not distinct lines."""

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, computed_field

from src.models.schemas.base import Model
from src.models.schemas.common import Money, ProductId
from src.models.schemas.product import Offer

Quantity = Annotated[int, Field(ge=1, le=999, strict=True)]
CartItemId = (
    Annotated[int, Field(gt=0, strict=True)]
    | Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$", strict=True)]
)
CartAction = Literal["add", "update", "remove"]
PlanId = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]


class CartLimits(Model):
    max_total_rial: Money
    max_items: Annotated[int, Field(ge=0, strict=True)]


class CartPolicy(Model):
    limits: CartLimits | None


class CartItem(Model):
    cart_item_id: CartItemId
    product_id: ProductId
    title: str
    quantity: Quantity
    offer: Offer


class CartSnapshot(Model):
    # MCP validates serialized output against this schema, including derived totals.
    model_config = ConfigDict(json_schema_mode_override="serialization")

    items: list[CartItem] = Field(default_factory=list)
    shipping_price_rial: Money | None = None
    has_unsupported_extras: bool = False
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @computed_field
    @property
    def total_items(self) -> int:
        return sum(item.quantity for item in self.items)

    @computed_field
    @property
    def items_total_rial(self) -> int | None:
        if any(item.offer.price_rial is None for item in self.items):
            return None
        return sum(item.quantity * (item.offer.price_rial or 0) for item in self.items)


class CartOffer(Model):
    product_id: ProductId
    title: str
    offer: Offer
    min_quantity: Quantity = 1
    max_quantity: Quantity | None = None


class CartChange(Model):
    action: CartAction
    product_id: ProductId | None = None
    offer_id: ProductId | None = None
    cart_item_id: CartItemId | None = None
    quantity: Quantity | None = None


class CartPlan(Model):
    plan_id: PlanId
    action: CartAction
    product_id: ProductId
    title: str
    offer: Offer
    cart_item_id: CartItemId | None = None
    previous_quantity: int
    target_quantity: int
    projected_total_rial: Money | None
    projected_items: int
    limits: CartLimits | None
    expires_at: datetime


class CartOperation(Model):
    plan_id: PlanId
    state: Literal["applied", "uncertain", "rejected"]
    cart: CartSnapshot | None = None
    reason: str | None = None
    limits_exceeded: bool | None = None
