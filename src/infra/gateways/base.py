from abc import ABC, abstractmethod

from src.infra.http import GatewayError, HTTPConnection
from src.models import (
    AutocompleteResult,
    Category,
    Location,
    Market,
    Product,
    SearchQuery,
    SearchResult,
)


class ABCMarketGateway(ABC):
    market: Market
    supports_autocomplete = False
    supports_categories = False

    def __init__(self, http: HTTPConnection):
        self.http = http

    async def list_categories(self) -> list[Category]:
        raise GatewayError(
            "capability_unknown", "Categories have not been verified for this market"
        )

    async def autocomplete(self, q: str) -> AutocompleteResult:
        raise GatewayError(
            "capability_unknown", "Autocomplete has not been verified for this market"
        )

    @abstractmethod
    async def search(
        self, query: SearchQuery, location: Location | None = None
    ) -> SearchResult: ...

    @abstractmethod
    async def get_product(self, product_id: str, location: Location | None = None) -> Product: ...
