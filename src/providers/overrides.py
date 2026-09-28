"""Explicit service overrides for embedded clients and integration tests."""

from collections.abc import AsyncGenerator

from dishka import Provider, Scope

from src.app.accounts import AccountService
from src.app.cart import CartService
from src.app.catalog import CatalogService
from src.providers.catalog import CatalogFactory


def service_overrides(
    catalog_factory: CatalogFactory | None,
    cart_service: CartService | None,
    account_service: AccountService | None,
) -> Provider:
    overrides = Provider(scope=Scope.APP)
    if catalog_factory is not None:

        async def catalog() -> AsyncGenerator[CatalogService, None]:
            async with catalog_factory() as service:
                yield service

        overrides.provide(catalog, override=True)
    if cart_service is not None:

        def cart() -> CartService:
            return cart_service

        overrides.provide(cart, override=True)
    if account_service is not None:

        def accounts() -> AccountService:
            return account_service

        overrides.provide(accounts, override=True)
    return overrides
