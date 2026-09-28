from datetime import datetime, timezone
from typing import Literal

from pydantic import Field

from src.models.schemas.base import Model
from src.models.schemas.market import MarketError
from src.models.schemas.product import Product


class TrendSnapshot(Model):
    source: Literal["digikala_home_best_selling"] = "digikala_home_best_selling"
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    title: str | None = None
    products: list[Product] = Field(default_factory=list)
    period: str | None = None
    error: MarketError | None = None
    limitation: str = (
        "Current homepage best-selling listing; no sales counts, "
        "historical growth, or category-specific trend proof."
    )
