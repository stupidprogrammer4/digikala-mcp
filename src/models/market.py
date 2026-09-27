from enum import StrEnum
from typing import Literal

from src.models.base import Model


class Market(StrEnum):
    DIGIKALA = "digikala"


class MarketError(Model):
    code: str
    message: str
    retryable: bool = False


class MarketInfo(Model):
    market: Market
    capabilities: dict[str, Literal["verified", "unknown"]]
    last_verified: str
    health_checked_now: bool = False
    location_required_by_api: bool = False


class MarketList(Model):
    markets: list[MarketInfo]
