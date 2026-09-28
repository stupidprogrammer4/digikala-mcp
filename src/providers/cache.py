"""Public observation cache lifetime."""

from collections.abc import AsyncGenerator

import httpx
from dishka import Provider, Scope, provide

from src.infra.cache import AsyncTTLCache


class CacheProvider(Provider):
    @provide(scope=Scope.APP)
    async def cache(self, client: httpx.AsyncClient) -> AsyncGenerator[AsyncTTLCache, None]:
        # Resolve HTTP first so in-flight cache tasks are finalized before its client.
        cache = AsyncTTLCache()
        try:
            yield cache
        finally:
            await cache.aclose()
