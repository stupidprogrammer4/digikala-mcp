"""Exercise the deployment migration path on isolated PostgreSQL schemas."""

import asyncio
import os
import sys

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from src.models.db import CartOperationRow
from tests.database_migrations import CONFIG, migrate_database
from tests.database_scopes import JournalClient
from tests.test_journal import record


async def test_migration_matches_sqlmodel_and_has_a_reversible_baseline(database):
    await migrate_database(database)
    async with database.connection() as connection:
        differences = await connection.run_sync(
            lambda sync: compare_metadata(
                MigrationContext.configure(sync), CartOperationRow.metadata
            )
        )
    assert differences == []
    await JournalClient(database).insert(record("first"))
    await migrate_database(database)  # An already-applied upgrade preserves all records.
    assert await JournalClient(database).find("first") is not None
    await migrate_database(database, "downgrade", "base")
    await migrate_database(database)
    assert await JournalClient(database).find("first") is None


async def test_alembic_cli_uses_database_url_without_project_main(database):
    assert database._dsn is not None
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "-c",
        str(CONFIG),
        "upgrade",
        "head",
        env=dict(os.environ, DATABASE_URL=database._dsn),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, error = await process.communicate()
    assert process.returncode == 0, error.decode()
    await JournalClient(database).insert(record("cli"))
    assert await JournalClient(database).find("cli") is not None
