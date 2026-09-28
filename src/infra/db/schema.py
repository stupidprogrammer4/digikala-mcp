"""Explicit, non-destructive provisioning from registered SQLModel metadata."""

from src.infra.db.session import Database
from src.models.db import CartOperationRow


async def initialize_database(database: Database) -> None:
    # create_all checks for existing tables; never drop/recreate a live operation journal.
    async with database.connection() as connection:
        await connection.run_sync(CartOperationRow.metadata.create_all)
