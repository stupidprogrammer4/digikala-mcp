"""Alembic owns schema changes; application startup never creates tables."""

import asyncio

from alembic import context
from sqlalchemy.engine import Connection

from src.config.settings import database_url
from src.infra.db import DBConnection
from src.infra.db.exceptions import DatabaseError
from src.models.db import CartOperationRow

metadata = CartOperationRow.metadata


def migrate(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def migrate_online() -> None:
    database = DBConnection(database_url())
    try:
        async with database.connection() as connection:
            await connection.run_sync(migrate)
    finally:
        await database.aclose()


if context.is_offline_mode():
    context.configure(
        dialect_name="postgresql",
        target_metadata=metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
elif (connection := context.config.attributes.get("connection")) is not None:
    migrate(connection)
else:
    try:
        asyncio.run(migrate_online())
    except DatabaseError as exc:
        raise SystemExit(f"{exc.code}: {exc.message}") from None
