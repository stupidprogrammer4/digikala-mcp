"""Public gateway and isolated authenticated HTTP gateway factories."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from dishka import Provider, Scope, provide

from src.app.accounts import ConnectedAccount, GatewayFactory
from src.app.cart import CartGatewayFactory
from src.infra.cache import AsyncTTLCache
from src.infra.http import HTTPConnection
from src.infra.http.gateways.cart import CartGateway, DigikalaCartGateway
from src.infra.http.gateways.digikala import DigikalaGateway
from src.infra.http.session import HEADERS, SessionStore


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


class GatewayProvider(Provider):
    scope = Scope.APP

    @provide
    def gateway(self, http: HTTPConnection, cache: AsyncTTLCache) -> DigikalaGateway:
        return DigikalaGateway(http, cache)

    @provide
    def cart_gateway(self) -> CartGatewayFactory:
        return open_cart_gateway

    @provide
    def account_gateway(self) -> GatewayFactory:
        return open_account_gateway
