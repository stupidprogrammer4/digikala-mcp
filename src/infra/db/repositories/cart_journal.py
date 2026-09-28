"""Cart SQL operations on an injected session; lifecycle belongs to Dishka."""

from copy import deepcopy

from sqlalchemy import func, update
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.infra.db.exceptions import OperationNotFound
from src.models.db import CartOperationRow, OperationState


class CartJournal:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find(self, plan_id: str) -> dict | None:
        row = await self.session.get(CartOperationRow, plan_id)
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
        self.session.add(row)
        await self.session.flush()
        return row.versioned_record()

    async def transition(
        self, record: dict, expected_state: OperationState, state: OperationState
    ) -> dict | None:
        """A single conditional UPDATE prevents stale claims or result overwrites."""
        payload = {key: deepcopy(value) for key, value in record.items() if key != "_revision"}
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
        row = (await self.session.exec(statement)).scalar_one_or_none()
        await self.session.flush()
        return row.versioned_record() if row is not None else None

    async def unresolved(self, connection_id: str) -> bool:
        statement = (
            select(CartOperationRow.id)
            .where(
                col(CartOperationRow.connection) == connection_id,
                col(CartOperationRow.state).in_(("executing", "uncertain")),
            )
            .limit(1)
        )
        return (await self.session.exec(statement)).first() is not None
