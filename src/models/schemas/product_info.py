"""Observed product ratings, seller metrics, and variant dimensions."""

from typing import Annotated, Literal

from pydantic import Field

from src.models.schemas.base import Model
from src.models.schemas.common import ProductId

Percent = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
Count = Annotated[int, Field(ge=0, strict=True)]


class VariantAttribute(Model):
    label: str
    value: str
    value_id: ProductId | None = None
    nature: str | None = None
    display_type: str | None = None
    code: str | None = None


class SellerRating(Model):
    satisfaction_percent: Percent | None = None
    rating_count: Count | None = None
    commitment_percent: Percent | None = None
    no_return_percent: Percent | None = None
    on_time_shipping_percent: Percent | None = None
    stars: float | None = Field(default=None, ge=0, le=5, allow_inf_nan=False)
    grade: str | None = None


class ProductRating(Model):
    score_percent: Percent | None = None
    rating_count: Count | None = None
    distribution_percent: dict[str, Percent] = Field(default_factory=dict)
    recommendation_percent: Percent | None = None
    recommendation_count: Count | None = None
    comments_count: Count | None = None
    questions_count: Count | None = None


class MediaAsset(Model):
    kind: Literal["image", "video"]
    url: str
    title: str | None = None
    cover_url: str | None = None
    is_main: bool = False
