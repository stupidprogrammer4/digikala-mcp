"""TTL, coalescing and cancellation behavior without sleeping through the TTL."""

import asyncio

import httpx
import pytest

from src.infra.cache import AsyncTTLCache
from src.infra.http import HTTPConnection
from src.infra.http.gateways.digikala import DigikalaGateway
from src.models.schemas.search import SearchQuery


async def test_expiration_is_not_extended_by_reads_and_values_are_isolated():
    now = [0.0]
    cache = AsyncTTLCache(clock=lambda: now[0])
    calls = 0

    async def load():
        nonlocal calls
        calls += 1
        return {"items": [calls]}

    first = await cache.get("a", load)
    first["items"].append(999)
    now[0] = 59.9
    assert await cache.get("a", load) == {"items": [1]}
    now[0] = 60
    assert await cache.get("a", load) == {"items": [2]}
    assert calls == 2


async def test_same_key_coalesces_without_blocking_other_keys():
    cache = AsyncTTLCache()
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def slow():
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return {"value": 1}

    async def fast():
        return {"value": 2}

    first = asyncio.create_task(cache.get("a", slow))
    await entered.wait()
    second = asyncio.create_task(cache.get("a", slow))
    assert await cache.get("b", fast) == {"value": 2}
    release.set()
    results = await asyncio.gather(first, second)
    assert calls == 1 and results[0] == results[1]
    assert results[0] is not results[1]


async def test_cancelled_waiter_does_not_cancel_shared_load():
    cache = AsyncTTLCache()
    entered, release = asyncio.Event(), asyncio.Event()

    async def load():
        entered.set()
        await release.wait()
        return 42

    first = asyncio.create_task(cache.get("a", load))
    await entered.wait()
    second = asyncio.create_task(cache.get("a", load))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert await second == 42
    assert not cache._flights


async def test_failures_are_not_cached_and_capacity_is_bounded():
    cache = AsyncTTLCache(max_entries=2)
    calls = 0

    async def load():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("failure")
        return calls

    with pytest.raises(ValueError):
        await cache.get("a", load)
    assert await cache.get("a", load) == 2
    await cache.get("b", load)
    await cache.get("c", load)
    assert len(cache._entries) == 2
    assert await cache.get("a", load) == 5
    await cache.aclose()
    assert not cache._entries


async def test_gateway_cache_keys_cover_query_page_sort_and_location(payload):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=payload("digikala-search"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        gateway = DigikalaGateway(HTTPConnection(client, "https://api.digikala.com"))
        await gateway.search(SearchQuery(query="a"))
        await gateway.search(query=SearchQuery(query="a"), location=None)
        await gateway.search(SearchQuery(query="b"))
        await gateway.search(SearchQuery(query="a", page=2))
        await gateway.search(SearchQuery(query="a", sort="price_asc"))
        assert len(calls) == 4
        await gateway.cache.aclose()


async def test_cart_http_reads_and_writes_are_never_cached():
    calls = []

    def handle(request):
        calls.append(request.method)
        return httpx.Response(200, json={"status": 200, "data": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        connection = HTTPConnection(client, "https://api.digikala.com")
        for method in ["GET", "GET", "POST", "PATCH", "DELETE"]:
            await connection.request(method, "/checkout/v1/carts/items")
    assert calls == ["GET", "GET", "POST", "PATCH", "DELETE"]
