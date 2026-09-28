"""Translate persistence failures at the MCP boundary, leaving infrastructure independent."""

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec, TypeVar

from fastmcp.exceptions import ToolError

from src.infra.db.exceptions import DatabaseError

P = ParamSpec("P")
T = TypeVar("T")


def handle_database_errors(function: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    @wraps(function)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return await function(*args, **kwargs)
        except DatabaseError as exc:
            # A failed journal write can follow a remote cart write: never claim rejection.
            raise ToolError(f"{exc.code}: {exc.message}") from None

    return wrapped
