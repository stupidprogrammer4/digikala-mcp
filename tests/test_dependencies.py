"""FastMCP/Dishka scopes, hidden parameters and deterministic resource cleanup."""

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from uuid import uuid4

import httpx
import pytest
from dishka import Provider, Scope, provide
from fastmcp import Client
from fastmcp.dependencies import Depends

from src.app.accounts import AccountService
from src.app.cart import CartService
from src.app.catalog import CatalogService
from src.bootstrap import create_container
from src.infra.cache import AsyncTTLCache
from src.server import create_server
from src.tools.dependencies import from_dishka


@dataclass
class Probe:
    identifier: str


async def test_request_scope_is_shared_within_call_and_closed_on_success_and_error():
    events = []

    class Probes(Provider):
        @provide(scope=Scope.REQUEST)
        async def probe(self) -> AsyncGenerator[Probe, None]:
            value = Probe(uuid4().hex)
            events.append(("opened", value.identifier))
            try:
                yield value
            finally:
                events.append(("closed", value.identifier))

    server = create_server(providers=[Probes()])

    @server.tool
    async def probe_scope(
        fail: bool = False,
        left: Probe = Depends(from_dishka(Probe)),
        right: Probe = Depends(from_dishka(Probe)),
    ) -> dict:
        assert left is right
        if fail:
            raise ValueError("probe failure")
        return {"identifier": left.identifier}

    async with Client(server) as client:
        tools = await client.list_tools()
        for tool in tools:
            assert not (
                {"service", "ctx", "container", "left", "right"}
                & tool.input_schema.get("properties", {}).keys()
            )
        results = await asyncio.gather(*(client.call_tool("probe_scope") for _ in range(3)))
        assert len({result.data["identifier"] for result in results}) == 3
        failed = await client.call_tool("probe_scope", {"fail": True}, raise_on_error=False)
        assert failed.is_error
    opened = [identifier for event, identifier in events if event == "opened"]
    closed = [identifier for event, identifier in events if event == "closed"]
    assert len(opened) == 4 and sorted(opened) == sorted(closed)


async def test_provider_graph_shares_journal_and_finalizes_cache_before_http(monkeypatch):
    container = create_container()
    events = []
    try:
        catalog = await container.get(CatalogService)
        cart = await container.get(CartService)
        accounts = await container.get(AccountService)
        assert cart.journal is accounts.replacer.journal
        assert await container.get(CatalogService) is catalog
        client = await container.get(httpx.AsyncClient)
        cache = await container.get(AsyncTTLCache)
        close_cache = cache.aclose

        async def cache_closed():
            assert not client.is_closed
            await close_cache()
            events.append("cache")

        monkeypatch.setattr(cache, "aclose", cache_closed)
    finally:
        await container.close()
    assert events == ["cache"]
    assert client.is_closed


async def test_app_scoped_dependencies_do_not_leak_between_servers():
    servers = [create_server(), create_server()]
    seen = []
    for server in servers:

        @server.tool
        async def service_identity(service: CatalogService = Depends(from_dishka(CatalogService))):
            seen.append(service)
            return {"identity": id(service)}

    async with Client(servers[0]) as first, Client(servers[1]) as second:
        a = await first.call_tool("service_identity")
        b = await first.call_tool("service_identity")
        c = await second.call_tool("service_identity")
        assert a.data == b.data and a.data != c.data
    assert seen[0] is seen[1] and seen[0] is not seen[2]


async def test_partial_startup_failure_closes_already_created_resources():
    clients = []

    class BrokenStartup(Provider):
        @provide(scope=Scope.APP, override=True)
        async def client(self) -> AsyncGenerator[httpx.AsyncClient, None]:
            async with httpx.AsyncClient() as client:
                clients.append(client)
                yield client

        @provide(scope=Scope.APP, override=True)
        def accounts(self) -> AccountService:
            raise RuntimeError("startup probe")

    with pytest.raises(Exception):
        async with Client(create_server(providers=[BrokenStartup()])):
            pytest.fail("Startup must fail before serving tools")
    assert len(clients) == 1 and clients[0].is_closed
