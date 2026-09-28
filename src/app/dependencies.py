"""Resource factories used by application services."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from src.infra.db.repositories import CartJournal

JournalFactory = Callable[[], AbstractAsyncContextManager[CartJournal]]
