"""Dishka owns database sessions and the commit boundary of each journal phase."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from dishka import AsyncContainer, Provider, Scope, provide
from dishka.exceptions import ExitError
from sqlmodel.ext.asyncio.session import AsyncSession

from src.app.dependencies import JournalFactory
from src.config.settings import database_url
from src.infra.db import DBConnection
from src.infra.db.errors import cart_conflicts, database_errors
from src.infra.db.exceptions import DatabaseUnavailable
from src.infra.db.repositories import CartJournal


class DatabaseProvider(Provider):
    @provide(scope=Scope.APP)
    async def database(self) -> AsyncGenerator[DBConnection, None]:
        database = DBConnection(database_url())
        try:
            yield database
        finally:
            await database.aclose()

    @provide(scope=Scope.REQUEST)
    async def session(self, database: DBConnection) -> AsyncGenerator[AsyncSession, None]:
        async with database.session() as session:
            yield session

    journal = provide(CartJournal, scope=Scope.REQUEST)

    @provide(scope=Scope.APP)
    def journal_factory(self, container: AsyncContainer, database: DBConnection) -> JournalFactory:
        # Resolve the shared connection before concurrent database scopes can start.
        @asynccontextmanager
        async def open_journal() -> AsyncGenerator[CartJournal, None]:
            with cart_conflicts(), database_errors():
                try:
                    async with container(lock_factory=None) as scope:
                        session = await scope.get(AsyncSession)
                        try:
                            yield await scope.get(CartJournal)
                            await session.commit()
                        except BaseException:
                            await session.rollback()
                            raise
                except ExitError:
                    raise DatabaseUnavailable() from None

        return open_journal
