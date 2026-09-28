"""Compose section-specific Dishka providers."""

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager

from dishka import AsyncContainer, Provider, make_async_container

from src.app.accounts import AccountService
from src.app.cart import CartService
from src.app.catalog import CatalogService
from src.providers.accounts import AccountProvider
from src.providers.cache import CacheProvider
from src.providers.cart import CartProvider
from src.providers.catalog import CatalogFactory, CatalogProvider
from src.providers.database import DatabaseProvider
from src.providers.gateways import GatewayProvider
from src.providers.http import HTTPProvider
from src.providers.overrides import service_overrides


def create_container(
    catalog_factory: CatalogFactory | None = None,
    cart_service: CartService | None = None,
    account_service: AccountService | None = None,
    providers: Sequence[Provider] = (),
) -> AsyncContainer:
    return make_async_container(
        HTTPProvider(),
        CacheProvider(),
        GatewayProvider(),
        DatabaseProvider(),
        CatalogProvider(),
        CartProvider(),
        AccountProvider(),
        service_overrides(catalog_factory, cart_service, account_service),
        *providers,
        lock_factory=None,
    )


@asynccontextmanager
async def open_catalog() -> AsyncGenerator[CatalogService, None]:
    """Local consumers can use the same provider graph without starting an MCP server."""
    container = create_container()
    try:
        yield await container.get(CatalogService)
    finally:
        await container.close()
