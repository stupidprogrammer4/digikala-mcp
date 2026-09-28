"""Bounded, process-local TTL cache for public catalog observations only."""

import asyncio
import copy
import inspect
import json
import time
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, TypeVar

from pydantic import BaseModel

from src.models.cache import CacheEntry

T = TypeVar("T")


class AsyncTTLCache:
    def __init__(
        self, ttl: float = 60, max_entries: int = 256, clock: Callable[[], float] = time.monotonic
    ):
        if ttl <= 0 or max_entries < 1:
            raise ValueError("Cache bounds must be positive")
        self.ttl = ttl
        self.max_entries = max_entries
        self.clock = clock
        self._entries: dict[str, CacheEntry] = {}
        self._flights: dict[str, asyncio.Task[Any]] = {}

    async def get(self, key: str, loader: Callable[[], Awaitable[T]]) -> T:
        now = self.clock()
        entry = self._entries.get(key)
        if entry is not None and now < entry.expires_at:
            return copy.deepcopy(entry.value)
        self._entries.pop(key, None)
        task = self._flights.get(key)
        if task is None:

            async def load():
                value = await loader()
                now = self.clock()
                for expired in [k for k, entry in self._entries.items() if entry.expires_at <= now]:
                    del self._entries[expired]
                if len(self._entries) >= self.max_entries:
                    del self._entries[next(iter(self._entries))]
                self._entries[key] = CacheEntry(now + self.ttl, copy.deepcopy(value))
                return value

            task = asyncio.create_task(load())
            self._flights[key] = task

            def finished(done: asyncio.Task):
                self._flights.pop(key, None)
                if not done.cancelled():
                    done.exception()  # Consume failures even when every waiter was cancelled.

            task.add_done_callback(finished)
        # One cancelled caller must not cancel another caller's shared observation.
        return copy.deepcopy(await asyncio.shield(task))

    async def aclose(self) -> None:
        tasks = list(self._flights.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._entries.clear()


def cached_catalog(fn):
    """Cache validated gateway results, preserving their original observation timestamps."""
    signature = inspect.signature(fn)

    def encode(value):
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        raise TypeError("Unsupported catalog cache key")

    @wraps(fn)
    async def wrapped(self, *args, **kwargs):
        bound = signature.bind(self, *args, **kwargs)
        bound.apply_defaults()
        arguments = {k: v for k, v in bound.arguments.items() if k != "self"}
        key = (
            fn.__name__
            + ":"
            + json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=encode)
        )
        return await self.cache.get(key, lambda: fn(self, *args, **kwargs))

    return wrapped
