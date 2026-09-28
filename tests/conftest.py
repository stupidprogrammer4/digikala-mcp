import copy
import json
from pathlib import Path

import httpx
import pytest

from src.app.catalog import CatalogService
from src.infra.http import HTTPConnection
from src.infra.http.gateways.digikala import DigikalaGateway


@pytest.fixture
def payload():
    def load(name):
        return json.loads((Path(__file__).parent / "fixtures" / f"{name}.json").read_text())

    return load


@pytest.fixture
async def catalog_factory():
    clients = []

    def make(handler):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        clients.append(client)
        return CatalogService(
            [
                DigikalaGateway(HTTPConnection(client, "https://api.digikala.com")),
            ]
        )

    yield make
    for client in clients:
        await client.aclose()


@pytest.fixture
def comparison_payload(payload):
    """Synthetic second seller added to the observed Digikala product fixture."""

    def load():
        data = payload("digikala-product")
        variants = data["data"]["product"]["variants"]
        second = copy.deepcopy(variants[0])
        second["id"] = 84016751
        second["seller"]["id"] = 999
        second["price"]["selling_price"] += 50000
        variants.append(second)
        return data

    return load


@pytest.fixture(scope="session")
def postgres_dsn(tmp_path_factory):
    """Use an explicit test database or start an isolated local PostgreSQL cluster."""
    import os
    import shutil
    import subprocess

    configured = os.environ.get("TEST_DATABASE_URL")
    if configured:
        yield configured
        return
    pg_config = shutil.which("pg_config")
    if not pg_config:
        pytest.skip("Set TEST_DATABASE_URL or install PostgreSQL server binaries")
    bindir = Path(subprocess.check_output([pg_config, "--bindir"], text=True).strip())
    if not (bindir / "initdb").exists() or os.getuid() == 0:
        pytest.skip("Set TEST_DATABASE_URL; local initdb requires server binaries and non-root")
    directory = tmp_path_factory.mktemp("postgres")
    data, socket = directory / "data", directory / "socket"
    socket.mkdir(mode=0o700)
    subprocess.run(
        [
            str(bindir / "initdb"),
            "-D",
            str(data),
            "--auth-local=trust",
            "--auth-host=reject",
            "--no-locale",
            "--encoding=UTF8",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            str(bindir / "pg_ctl"),
            "-D",
            str(data),
            "-l",
            str(directory / "server.log"),
            "-o",
            f"-c listen_addresses='' -k {socket} -p 55439",
            "-w",
            "start",
        ],
        check=True,
        capture_output=True,
    )
    try:
        yield f"host={socket} port=55439 dbname=postgres"
    finally:
        subprocess.run(
            [str(bindir / "pg_ctl"), "-D", str(data), "-m", "fast", "-w", "stop"],
            check=True,
            capture_output=True,
        )


@pytest.fixture
async def database(postgres_dsn):
    from uuid import uuid4

    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    from src.infra.db import Database

    schema = "test_" + uuid4().hex
    async with await psycopg.AsyncConnection.connect(postgres_dsn, autocommit=True) as db:
        await db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    database = Database(make_conninfo(postgres_dsn, options=f"-c search_path={schema}"))
    try:
        yield database
    finally:
        await database.aclose()
        async with await psycopg.AsyncConnection.connect(postgres_dsn, autocommit=True) as db:
            await db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
async def journal(database):
    from src.infra.db import CartJournal
    from src.infra.db.schema import initialize_database

    await initialize_database(database)
    return CartJournal(database)


@pytest.fixture
def cart_setup(journal):
    from contextlib import asynccontextmanager

    from src.app.cart import CartService
    from src.models.schemas.cart import CartLimits
    from tests.test_cart import FakeCart

    gateway = FakeCart()

    @asynccontextmanager
    async def open_gateway():
        yield gateway

    service = CartService(open_gateway, journal, CartLimits(max_total_rial=300, max_items=3))
    return service, gateway
