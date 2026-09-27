import copy
import json
from pathlib import Path

import httpx
import pytest

from src.app.catalog import CatalogService
from src.infra.gateways.digikala import DigikalaGateway
from src.infra.http import HTTPConnection


@pytest.fixture
def payload():
    def load(name):
        return json.loads((Path(__file__).parent / "fixtures" / f"{name}.json").read_text())

    return load


@pytest.fixture
async def catalog_factory():
    clients = []

    def make(handler):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        clients.append(client)
        return CatalogService(
            [
                DigikalaGateway(HTTPConnection(client, "https://api.digikala.com")),
            ]
        )

    yield make
    for client in clients:
        await client.aclose()


@pytest.fixture
def comparison_payload(payload):
    """Synthetic second seller added to the observed Digikala product fixture."""

    def load():
        data = payload("digikala-product")
        variants = data["data"]["product"]["variants"]
        second = copy.deepcopy(variants[0])
        second["id"] = 84016751
        second["seller"]["id"] = 999
        second["price"]["selling_price"] += 50000
        variants.append(second)
        return data

    return load
