"""Durable operation records plus a process lock, for local Linux/macOS stdio use."""

import fcntl
import json
import os
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from src.infra.http import GatewayError


class CartJournal:
    def __init__(self, directory: Path):
        self.directory = directory

    @contextmanager
    def locked(self) -> Generator[sqlite3.Connection, None, None]:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.directory / "cart.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise GatewayError(
                    "cart_busy", "Another local process is changing the cart"
                ) from None
            db_path = self.directory / "cart.sqlite3"
            fd = os.open(db_path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)
            db = sqlite3.connect(db_path)
            try:
                db.execute("PRAGMA synchronous=FULL")
                db.execute(
                    "CREATE TABLE IF NOT EXISTS operations "
                    "(id TEXT PRIMARY KEY, connection TEXT NOT NULL, state TEXT NOT NULL, "
                    "record TEXT NOT NULL)"
                )
                db.commit()
                yield db
            finally:
                db.close()
        finally:
            os.close(descriptor)

    @staticmethod
    def put(db: sqlite3.Connection, record: dict) -> None:
        db.execute(
            "INSERT OR REPLACE INTO operations VALUES (?, ?, ?, ?)",
            (
                record["plan"]["plan_id"],
                record["connection_id"],
                record["state"],
                json.dumps(record),
            ),
        )
        db.commit()

    @staticmethod
    def get(db: sqlite3.Connection, plan_id: str) -> dict:
        row = db.execute("SELECT record FROM operations WHERE id=?", (plan_id,)).fetchone()
        if row is None:
            raise GatewayError("plan_not_found", "Prepare a cart change first")
        return json.loads(row[0])

    @staticmethod
    def unresolved(db: sqlite3.Connection, connection_id: str) -> bool:
        return (
            db.execute(
                "SELECT 1 FROM operations WHERE connection=? "
                "AND state IN ('executing', 'uncertain')",
                (connection_id,),
            ).fetchone()
            is not None
        )
