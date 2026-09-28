"""Lazy PostgreSQL engine and one transactional SQLModel session per operation."""

from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager

from psycopg import ProgrammingError
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError, IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from src.infra.db.exceptions import (
    DatabaseClosed,
    DatabaseConfigurationError,
    DatabaseIntegrityError,
    DatabaseNotConfigured,
    DatabaseUnavailable,
)


@contextmanager
def database_errors() -> Generator[None, None, None]:
    """Never expose driver messages, SQL statements, parameters or connection credentials."""
    try:
        yield
    except IntegrityError as exc:
        diagnostic = getattr(exc.orig, "diag", None)
        raise DatabaseIntegrityError(
            constraint=getattr(diagnostic, "constraint_name", None),
            sqlstate=getattr(exc.orig, "sqlstate", None),
        ) from None
    except SQLAlchemyError:
        raise DatabaseUnavailable() from None


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


class Database:
    """Application-owned resource; constructing it never connects or creates tables."""

    def __init__(self, dsn: str | None):
        self._dsn = dsn
        self._engine: AsyncEngine | None = None
        self._closed = False

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

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """A session is never shared across tasks; commit or roll back at the boundary."""
        with database_errors():
            async with AsyncSession(self.engine, expire_on_commit=False) as session:
                async with session.begin():
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
