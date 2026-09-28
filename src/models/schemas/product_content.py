"""Public product content with explicit source, coverage, and observation time."""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from src.models.schemas.base import Model
from src.models.schemas.common import ProductId
from src.models.schemas.market import MarketError
from src.models.schemas.product import Offer, Product
from src.models.schemas.product_info import Count, ProductRating, SellerRating

Page = Annotated[int, Field(ge=1, le=1000, strict=True)]
ReviewSort = Literal["default", "newest", "buyers"]
QuestionSort = Literal["created_at", "answers"]
SectionKey = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")]


class ProductObservation(Model):
    product_id: ProductId
    source_url: str | None = None
    observed_at: datetime | None = None
    cache_ttl_seconds: Literal[60] = 60
    warnings: list[str] = Field(default_factory=list)
    error: MarketError | None = None


class Pagination(Model):
    current_page: Page
    total_pages: Count
    total_items: Count


class ProductReview(Model):
    review_id: ProductId
    title: str | None = None
    body: str
    rating: float | None = Field(default=None, ge=0, le=5, allow_inf_nan=False)
    is_buyer: bool | None = None
    created_at_text: str | None = None
    recommendation_status: str | None = None
    advantages: list[str] = Field(default_factory=list)
    disadvantages: list[str] = Field(default_factory=list)
    likes: Count | None = None
    dislikes: Count | None = None


class ReviewListResult(ProductObservation):
    page: Page = 1
    sort: ReviewSort = "default"
    pager: Pagination | None = None
    reviews: list[ProductReview] = Field(default_factory=list)


class ProductAnswer(Model):
    answer_id: ProductId
    text: str
    created_at_text: str | None = None
    responder_type: str | None = None
    seller_id: ProductId | None = None
    likes: Count | None = None
    dislikes: Count | None = None


class ProductQuestion(Model):
    question_id: ProductId
    text: str
    answer_count: Count | None = None
    created_at_text: str | None = None
    answers: list[ProductAnswer] = Field(default_factory=list)


class QuestionListResult(ProductObservation):
    page: Page = 1
    sort: QuestionSort = "created_at"
    pager: Pagination | None = None
    questions: list[ProductQuestion] = Field(default_factory=list)


class RatingResult(ProductObservation):
    rating: ProductRating | None = None


class VariantListResult(ProductObservation):
    coverage: Literal["variants_endpoint"] = "variants_endpoint"
    variants: list[Offer] = Field(default_factory=list)


class VariantValue(Model):
    value_id: ProductId | None = None
    title: str
    code: str | None = None
    offer_ids: list[str] = Field(default_factory=list)


class VariantType(Model):
    label: str
    nature: str | None = None
    display_type: str | None = None
    values: list[VariantValue] = Field(default_factory=list)


class VariantTypeListResult(ProductObservation):
    coverage: Literal["variants_endpoint"] = "variants_endpoint"
    types: list[VariantType] = Field(default_factory=list)


class ProductSeller(Model):
    seller_id: ProductId
    name: str | None = None
    rating: SellerRating | None = None
    offers: list[Offer] = Field(default_factory=list)


class SellerListResult(ProductObservation):
    coverage: Literal["variants_endpoint"] = "variants_endpoint"
    sellers: list[ProductSeller] = Field(default_factory=list)
    unidentified_offers: list[Offer] = Field(default_factory=list)


class SellerComparisonQuery(Model):
    product_id: ProductId
    offer_ids: list[ProductId] = Field(min_length=2, max_length=6)

    @model_validator(mode="after")
    def distinct_offers(self) -> Self:
        if len(self.offer_ids) != len(set(self.offer_ids)):
            raise ValueError("Select distinct seller offer IDs")
        return self


class RecommendationSection(Model):
    key: SectionKey
    type: str
    template: str | None = None


class RecommendationSections(ProductObservation):
    sections: list[RecommendationSection] = Field(default_factory=list)


class RecommendationResult(ProductObservation):
    section_key: SectionKey = "similar_products"
    title: str | None = None
    available_sections: list[RecommendationSection] = Field(default_factory=list)
    products: list[Product] = Field(default_factory=list)
    source_count: Count | None = None
