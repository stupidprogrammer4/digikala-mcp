"""SQLModel repository for atomic cart claims and versioned state transitions."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from copy import deepcopy

from sqlalchemy import func, update
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.infra.db.exceptions import (
    DatabaseIntegrityError,
    OperationConflict,
    OperationNotFound,
    UnresolvedOperation,
)
from src.infra.db.session import Database
from src.models.db import CartOperationRow, OperationState


class CartJournal:
    def __init__(self, database: Database):
        self.database = database

    @asynccontextmanager
    async def _session(self) -> AsyncGenerator[AsyncSession, None]:
        try:
            async with self.database.session() as session:
                yield session
        except DatabaseIntegrityError as exc:
            if exc.sqlstate == "23505":
                if exc.constraint == "cart_one_unresolved_operation":
                    raise UnresolvedOperation() from None
                if exc.constraint == "cart_operations_pkey":
                    raise OperationConflict() from None
            raise

    async def find(self, plan_id: str) -> dict | None:
        async with self._session() as session:
            row = await session.get(CartOperationRow, plan_id)
            return row.versioned_record() if row is not None else None

    async def get(self, plan_id: str) -> dict:
        record = await self.find(plan_id)
        if record is None:
            raise OperationNotFound()
        return record

    async def insert(self, record: dict) -> dict:
        row = CartOperationRow.model_validate(
            {
                "id": record["plan"]["plan_id"],
                "connection": record["connection_id"],
                "state": record["state"],
                "record": deepcopy(record),
            }
        )
        async with self._session() as session:
            session.add(row)
            await session.flush()
            result = row.versioned_record()
        return result

    async def transition(
        self, record: dict, expected_state: OperationState, state: OperationState
    ) -> dict | None:
        """One conditional UPDATE: a stale revision cannot claim or finish an operation."""
        payload = {k: deepcopy(v) for k, v in record.items() if k != "_revision"}
        payload["state"] = state
        statement = (
            update(CartOperationRow)
            .where(
                col(CartOperationRow.id) == record["plan"]["plan_id"],
                col(CartOperationRow.connection) == record["connection_id"],
                col(CartOperationRow.state) == expected_state,
                col(CartOperationRow.revision) == record["_revision"],
            )
            .values(
                state=state,
                record=payload,
                revision=col(CartOperationRow.revision) + 1,
                updated_at=func.now(),
            )
            .returning(CartOperationRow)
        )
        async with self._session() as session:
            result = await session.exec(statement)
            row = result.scalar_one_or_none()
            versioned = row.versioned_record() if row is not None else None
        return versioned

    async def unresolved(self, connection_id: str) -> bool:
        statement = (
            select(CartOperationRow.id)
            .where(
                col(CartOperationRow.connection) == connection_id,
                col(CartOperationRow.state).in_(("executing", "uncertain")),
            )
            .limit(1)
        )
        async with self._session() as session:
            return (await session.exec(statement)).first() is not None
