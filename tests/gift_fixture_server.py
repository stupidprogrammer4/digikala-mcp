"""Real catalog/account MCP services over a synthetic external catalog/cart."""

from contextlib import asynccontextmanager

import httpx

from src.app.catalog import CatalogService
from src.infra.http import HTTPConnection
from src.infra.http.gateways.base import ABCMarketGateway
from src.models.schemas.market import Market
from src.models.schemas.product import Product
from src.models.schemas.search import SearchResult
from src.server import create_server
from tests.account_fixture_server import SyntheticAccounts, selected


class SyntheticCatalog(ABCMarketGateway):
    market = Market.DIGIKALA

    async def get_product(self, product_id, location=None):
        return Product(
            market=self.market,
            product_id=product_id,
            title={"1": "دستبند نقره", "2": "گردنبند نقره", "3": "انگشتر نقره نگین"}[product_id],
            url=f"https://www.digikala.com/product/dkp-{product_id}/",
            price_rial=100,
            availability="available",
            offers=[selected(product_id + "1")],
        )

    async def search(self, query, location=None):
        import asyncio

        products = await asyncio.gather(*(self.get_product(str(number)) for number in range(1, 4)))
        return SearchResult(market=self.market, products=list(products), page=1, total_pages=1)


@asynccontextmanager
async def catalog():
    async with httpx.AsyncClient() as client:
        yield CatalogService([SyntheticCatalog(HTTPConnection(client, "https://unused.example"))])


def create_fixture_server():
    return create_server(catalog_factory=catalog, providers=[SyntheticAccounts()])
