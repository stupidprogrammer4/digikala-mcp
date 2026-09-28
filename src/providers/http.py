"""Shared public HTTP client and connection."""

from collections.abc import AsyncGenerator

import httpx
from dishka import Provider, Scope, provide

from src.infra.http import HTTPConnection


class HTTPProvider(Provider):
    scope = Scope.APP

    @provide
    async def client(self) -> AsyncGenerator[httpx.AsyncClient, None]:
        async with httpx.AsyncClient(
            headers={"Accept": "application/json"}, limits=httpx.Limits(max_connections=6)
        ) as client:
            yield client

    @provide
    def connection(self, client: httpx.AsyncClient) -> HTTPConnection:
        return HTTPConnection(client, "https://api.digikala.com")
