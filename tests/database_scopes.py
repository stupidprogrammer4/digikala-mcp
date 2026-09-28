"""Test convenience client; every call uses the production Dishka session providers."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from dishka import Provider, Scope, make_async_container

from src.app.dependencies import JournalFactory
from src.infra.db import DBConnection
from src.infra.db.repositories import CartJournal
from src.models.db import OperationState
from src.providers.database import DatabaseProvider


class JournalClient:
    def __init__(self, database: DBConnection):
        self.database = database

    @asynccontextmanager
    async def __call__(self) -> AsyncGenerator[CartJournal, None]:
        def connection() -> DBConnection:
            return self.database

        overrides = Provider(scope=Scope.APP)
        overrides.provide(connection, override=True)
        container = make_async_container(DatabaseProvider(), overrides, lock_factory=None)
        try:
            factory: JournalFactory = await container.get(JournalFactory)
            async with factory() as journal:
                yield journal
        finally:
            await container.close()

    async def find(self, key: str) -> dict | None:
        async with self() as journal:
            return await journal.find(key)

    async def get(self, key: str) -> dict:
        async with self() as journal:
            return await journal.get(key)

    async def insert(self, record: dict) -> dict:
        async with self() as journal:
            return await journal.insert(record)

    async def transition(
        self, record: dict, expected_state: OperationState, state: OperationState
    ) -> dict | None:
        async with self() as journal:
            return await journal.transition(record, expected_state, state)

    async def unresolved(self, connection_id: str) -> bool:
        async with self() as journal:
            return await journal.unresolved(connection_id)
