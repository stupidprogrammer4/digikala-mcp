from pydantic import Field

from src.models.base import Model
from src.models.category import CategoryId
from src.models.market import Market, MarketError


class Suggestion(Model):
    text: str
    category: str | None = None
    category_id: CategoryId | None = None


class AutocompleteResult(Model):
    market: Market
    suggestions: list[Suggestion] = Field(default_factory=list)
    error: MarketError | None = None
