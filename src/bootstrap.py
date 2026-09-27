"""Compose application dependencies and own their lifetime."""

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx

from src.app.cart import CartService
from src.app.catalog import CatalogService
from src.config.settings import cart_limits, state_directory
from src.infra.cart_journal import CartJournal
from src.infra.gateways.cart import CartGateway, DigikalaCartGateway
from src.infra.gateways.digikala import DigikalaGateway
from src.infra.http import HTTPConnection
from src.infra.session import HEADERS, SessionStore

CatalogFactory = Callable[[], AbstractAsyncContextManager[CatalogService]]


@asynccontextmanager
async def open_catalog() -> AsyncGenerator[CatalogService, None]:
    async with httpx.AsyncClient(
        headers={"Accept": "application/json"}, limits=httpx.Limits(max_connections=6)
    ) as client:
        yield CatalogService(
            [
                DigikalaGateway(HTTPConnection(client, "https://api.digikala.com")),
            ]
        )


@asynccontextmanager
async def open_cart_gateway() -> AsyncGenerator[CartGateway, None]:
    store = SessionStore()
    session = await asyncio.to_thread(store.load)
    async with httpx.AsyncClient(headers=HEADERS, cookies=session.cookies) as client:
        yield DigikalaCartGateway(
            HTTPConnection(client, "https://api.digikala.com"), session.connection_id
        )


def create_cart_service() -> CartService:
    return CartService(open_cart_gateway, CartJournal(state_directory()), cart_limits())
