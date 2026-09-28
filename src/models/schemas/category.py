from typing import Annotated, Self

from pydantic import Field, model_validator

from src.models.schemas.base import Model
from src.models.schemas.common import QueryText
from src.models.schemas.market import Market, MarketError

CategoryId = Annotated[str, Field(pattern=r"^[1-9][0-9]{0,19}$")]


class Category(Model):
    category_id: CategoryId
    title: str
    title_en: str | None = None
    code: str
    parent_id: CategoryId | None = None
    has_children: bool = False


class CategoryQuery(Model):
    query: QueryText | None = None
    parent_id: CategoryId | None = None
    roots_only: bool = False
    page: int = Field(default=1, ge=1, strict=True)
    page_size: int = Field(default=50, ge=1, le=100, strict=True)

    @model_validator(mode="after")
    def valid_parent(self) -> Self:
        if self.roots_only and self.parent_id is not None:
            raise ValueError("roots_only and parent_id cannot be combined")
        return self


class CategoryListResult(Model):
    market: Market = Market.DIGIKALA
    categories: list[Category] = Field(default_factory=list)
    page: int
    page_size: int
    total_items: int | None = None
    total_pages: int | None = None
    error: MarketError | None = None
