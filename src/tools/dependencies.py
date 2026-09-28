"""Bridge FastMCP dependency scopes to Dishka without exposing services in tool schemas."""

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TypeVar

from dishka import AsyncContainer
from fastmcp import Context
from fastmcp.dependencies import CurrentContext, Depends

T = TypeVar("T")


@asynccontextmanager
async def request_container(
    ctx: Context = CurrentContext(),
) -> AsyncGenerator[AsyncContainer, None]:
    root: AsyncContainer = ctx.lifespan_context["container"]
    async with root(lock_factory=None) as request:
        yield request


def from_dishka(dependency: type[T]) -> Callable[..., Awaitable[T]]:
    async def resolve(container: AsyncContainer = Depends(request_container)) -> T:
        return await container.get(dependency)

    return resolve
