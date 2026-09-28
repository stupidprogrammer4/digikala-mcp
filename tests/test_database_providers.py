"""Real PostgreSQL checks for Dishka ownership above injected repositories."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from dishka import Provider, Scope, make_async_container, provide
from fastmcp import Client
from sqlalchemy.exc import InvalidRequestError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.app.cart import CartGatewayFactory
from src.app.dependencies import JournalFactory
from src.infra.db import DBConnection
from src.infra.db.repositories import CartJournal
from src.providers.database import DatabaseProvider
from src.server import create_server
from tests.database_migrations import migrate_database
from tests.test_cart import FakeCart
from tests.test_journal import record


@pytest.fixture
async def database_container(database):
    await migrate_database(database)

    class TestDatabase(Provider):
        @provide(scope=Scope.APP, override=True)
        def connection(self) -> DBConnection:
            return database

    container = make_async_container(DatabaseProvider(), TestDatabase(), lock_factory=None)
    try:
        yield container
    finally:
        await container.close()


async def test_repository_uses_injected_session_without_implicit_commit(database_container):
    factory: JournalFactory = await database_container.get(JournalFactory)
    async with database_container(lock_factory=None) as scope:
        repository = await scope.get(CartJournal)
        session = await scope.get(AsyncSession)
        assert repository.session is session
        assert await scope.get(CartJournal) is repository
        assert await scope.get(AsyncSession) is session
        await repository.insert(record("uncommitted"))
        async with factory() as observer:
            assert await observer.find("uncommitted") is None
    # Closing an unmanaged read scope never silently commits a repository write.
    async with factory() as observer:
        assert await observer.find("uncommitted") is None
    with pytest.raises(InvalidRequestError):
        await session.exec(select(1))


async def test_provider_commits_only_after_successful_database_phase(database_container):
    factory: JournalFactory = await database_container.get(JournalFactory)
    async with factory() as repository:
        await repository.insert(record("committed"))
        async with factory() as observer:
            assert await observer.find("committed") is None
    async with factory() as observer:
        assert await observer.find("committed") is not None
    with pytest.raises(ValueError, match="abort"):
        async with factory() as repository:
            await repository.insert(record("discarded"))
            raise ValueError("abort")
    async with factory() as observer:
        assert await observer.find("discarded") is None


async def test_concurrent_phases_have_distinct_sessions(database_container):
    factory: JournalFactory = await database_container.get(JournalFactory)
    sessions = []
    barrier = asyncio.Barrier(3)

    async def write(index):
        async with factory() as repository:
            sessions.append(repository.session)
            await barrier.wait()
            await repository.insert(record(str(index)))

    await asyncio.wait_for(asyncio.gather(*(write(i) for i in range(3))), 10)
    assert len({id(session) for session in sessions}) == 3
    async with factory() as observer:
        assert all([await observer.find(str(i)) is not None for i in range(3)])


async def test_cancelled_phase_rolls_back_and_releases_connection(database_container, database):
    factory: JournalFactory = await database_container.get(JournalFactory)
    started = asyncio.Event()

    async def write():
        async with factory() as repository:
            await repository.insert(record("cancelled"))
            started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(write())
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert getattr(database.engine.pool, "checkedout")() == 0
    async with factory() as observer:
        assert await observer.find("cancelled") is None


async def test_mcp_cart_uses_real_provider_graph_and_scoped_sessions(database, monkeypatch):
    await migrate_database(database)
    monkeypatch.setenv("INCART_CART_MAX_TOTAL_RIAL", "300")
    monkeypatch.setenv("INCART_CART_MAX_ITEMS", "3")
    gateway = FakeCart()

    @asynccontextmanager
    async def open_gateway():
        yield gateway

    class TestResources(Provider):
        @provide(scope=Scope.APP, override=True)
        def connection(self) -> DBConnection:
            return database

        @provide(scope=Scope.APP, override=True)
        def gateway_factory(self) -> CartGatewayFactory:
            return open_gateway

    async with Client(create_server(providers=[TestResources()])) as client:
        preview = await client.call_tool(
            "prepare_cart_change",
            {"change": {"action": "add", "product_id": "1", "offer_id": "10"}},
        )
        assert preview.structured_content is not None
        arguments = {"plan_id": preview.structured_content["plan_id"]}
        result = await client.call_tool("add_to_cart", arguments)
        assert result.structured_content is not None
        assert result.structured_content["state"] == "applied"
        retry = await client.call_tool("add_to_cart", arguments)
        assert retry.structured_content is not None
        assert retry.structured_content["state"] == "applied"
    assert gateway.writes == ["add"]
    assert getattr(database.engine.pool, "checkedout")() == 0
