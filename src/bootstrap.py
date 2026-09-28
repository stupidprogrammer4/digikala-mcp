"""Dishka providers own application dependencies and their cleanup."""

import asyncio
from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx
from dishka import AsyncContainer, Provider, Scope, make_async_container, provide

from src.app.accounts import AccountService, ConnectedAccount
from src.app.cart import CartService
from src.app.catalog import CatalogService
from src.app.products import ProductService
from src.app.replacement import CartReplacer
from src.config.settings import cart_limits, database_url
from src.infra.cache import AsyncTTLCache
from src.infra.db import CartJournal, Database
from src.infra.http import HTTPConnection
from src.infra.http.account_login import PasswordLogin
from src.infra.http.gateways.cart import CartGateway, DigikalaCartGateway
from src.infra.http.gateways.digikala import DigikalaGateway
from src.infra.http.session import HEADERS, SessionStore

CatalogFactory = Callable[[], AbstractAsyncContextManager[CatalogService]]


@asynccontextmanager
async def open_cart_gateway() -> AsyncGenerator[CartGateway, None]:
    session = await asyncio.to_thread(SessionStore().load)
    async with httpx.AsyncClient(headers=HEADERS, cookies=session.cookies) as client:
        yield DigikalaCartGateway(
            HTTPConnection(client, "https://api.digikala.com"), session.connection_id
        )


@asynccontextmanager
async def open_account_gateway(session: ConnectedAccount) -> AsyncGenerator[CartGateway, None]:
    async with httpx.AsyncClient(headers=HEADERS, cookies=session.cookies) as client:
        yield DigikalaCartGateway(
            HTTPConnection(client, "https://api.digikala.com"), session.connection_id
        )


class ApplicationProvider(Provider):
    scope = Scope.APP

    @provide
    async def http_client(self) -> AsyncGenerator[httpx.AsyncClient, None]:
        async with httpx.AsyncClient(
            headers={"Accept": "application/json"}, limits=httpx.Limits(max_connections=6)
        ) as client:
            yield client

    @provide
    def connection(self, client: httpx.AsyncClient) -> HTTPConnection:
        return HTTPConnection(client, "https://api.digikala.com")

    @provide
    async def cache(self) -> AsyncGenerator[AsyncTTLCache, None]:
        cache = AsyncTTLCache()
        try:
            yield cache
        finally:
            await cache.aclose()

    @provide
    def gateway(self, http: HTTPConnection, cache: AsyncTTLCache) -> DigikalaGateway:
        return DigikalaGateway(http, cache)

    @provide
    def catalog(self, gateway: DigikalaGateway) -> CatalogService:
        return CatalogService([gateway])

    @provide
    def products(self, catalog: CatalogService) -> ProductService:
        return catalog.products

    @provide
    async def database(self) -> AsyncGenerator[Database, None]:
        database = Database(database_url())
        try:
            yield database
        finally:
            await database.aclose()

    @provide
    def journal(self, database: Database) -> CartJournal:
        return CartJournal(database)

    @provide
    def cart(self, journal: CartJournal) -> CartService:
        return CartService(open_cart_gateway, journal, cart_limits())

    @provide
    def replacer(self, journal: CartJournal) -> CartReplacer:
        return CartReplacer(journal, cart_limits())

    login = provide(PasswordLogin)

    @provide
    async def accounts(
        self, login: PasswordLogin, replacer: CartReplacer
    ) -> AsyncGenerator[AccountService, None]:
        service = AccountService(login, open_account_gateway, replacer)
        try:
            yield service
        finally:
            service.sessions.clear()


def create_container(
    catalog_factory: CatalogFactory | None = None,
    cart_service: CartService | None = None,
    account_service: AccountService | None = None,
    providers: Sequence[Provider] = (),
) -> AsyncContainer:
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
    # APP dependencies are resolved during startup, before requests can race to create them.
    return make_async_container(ApplicationProvider(), overrides, *providers, lock_factory=None)


@asynccontextmanager
async def open_catalog() -> AsyncGenerator[CatalogService, None]:
    """Local consumers can use the same provider graph without starting an MCP server."""
    container = create_container()
    try:
        yield await container.get(CatalogService)
    finally:
        await container.close()
