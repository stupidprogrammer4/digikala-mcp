"""Run the production Alembic environment against an isolated test connection."""

from pathlib import Path
from typing import Literal

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection

from src.infra.db import DBConnection

CONFIG = Path(__file__).resolve().parents[1] / "alembic.ini"


async def migrate_database(
    database: DBConnection,
    action: Literal["upgrade", "downgrade", "stamp"] = "upgrade",
    revision: str = "head",
) -> None:
    def run(connection: Connection) -> None:
        config = Config(str(CONFIG))
        config.attributes["connection"] = connection
        operations = {
            "upgrade": command.upgrade,
            "downgrade": command.downgrade,
            "stamp": command.stamp,
        }
        operations[action](config, revision)

    async with database.connection() as connection:
        await connection.run_sync(run)
