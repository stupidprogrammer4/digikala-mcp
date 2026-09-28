"""Provision the cart journal: python -m src.infra.db."""

import asyncio

from src.config.settings import database_url
from src.infra.db.exceptions import DatabaseError
from src.infra.db.schema import initialize_database
from src.infra.db.session import Database


async def provision() -> None:
    database = Database(database_url())
    try:
        await initialize_database(database)
    finally:
        await database.aclose()


def main() -> None:
    try:
        asyncio.run(provision())
    except DatabaseError as exc:
        raise SystemExit(f"{exc.code}: {exc.message}") from None
    print("PostgreSQL cart journal initialized.")


if __name__ == "__main__":
    main()
