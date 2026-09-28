"""Application-owned PostgreSQL engine, pool and SQLModel session factory."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from psycopg import ProgrammingError
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel.ext.asyncio.session import AsyncSession

from src.infra.db.errors import database_errors
from src.infra.db.exceptions import (
    DatabaseClosed,
    DatabaseConfigurationError,
    DatabaseNotConfigured,
)


def connection_options(dsn: str) -> tuple[URL, dict]:
    """Support DATABASE_URL and existing libpq conninfo, including Unix-socket tests."""
    try:
        if "://" in dsn:
            url = make_url(dsn)
            if url.drivername not in {"postgres", "postgresql", "postgresql+psycopg"}:
                raise DatabaseConfigurationError()
            return url.set(drivername="postgresql+psycopg"), {"connect_timeout": 5}
        options = conninfo_to_dict(dsn)
        options["connect_timeout"] = "5"
        return URL.create("postgresql+psycopg"), options
    except (ArgumentError, ProgrammingError, ValueError, TypeError):
        raise DatabaseConfigurationError() from None


class DBConnection:
    """Application-owned resource; constructing it never connects or creates tables."""

    def __init__(self, dsn: str | None):
        self._dsn = dsn
        self._engine: AsyncEngine | None = None
        self._closed = False
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    @property
    def engine(self) -> AsyncEngine:
        if self._closed:
            raise DatabaseClosed()
        if not self._dsn:
            raise DatabaseNotConfigured()
        if self._engine is None:
            url, options = connection_options(self._dsn)
            self._engine = create_async_engine(
                url,
                connect_args=options,
                pool_size=5,
                max_overflow=0,
                pool_timeout=5,
                pool_pre_ping=True,
                hide_parameters=True,
                echo=False,
            )
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        engine = self.engine  # Also checks that the connection has not been closed.
        if self._session_factory is None:
            self._session_factory = async_sessionmaker(
                engine,
                class_=AsyncSession,
                autoflush=False,
                expire_on_commit=False,
                close_resets_only=False,
            )
        return self._session_factory

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """Open a fresh session; the caller owns commit or rollback."""
        with database_errors():
            async with self.session_factory() as session:
                yield session

    @asynccontextmanager
    async def connection(self) -> AsyncGenerator[AsyncConnection, None]:
        """Transactional connection for explicit schema provisioning."""
        with database_errors():
            async with self.engine.begin() as connection:
                yield connection

    async def aclose(self) -> None:
        self._closed = True
        if self._engine is not None:
            await self._engine.dispose()
