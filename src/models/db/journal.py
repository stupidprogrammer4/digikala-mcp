"""SQLModel mapping for persisted cart operations, independent of transport schemas."""

from copy import deepcopy
from datetime import datetime
from typing import Any, ClassVar, Literal

from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

OperationState = Literal["prepared", "executing", "uncertain", "applied", "rejected"]

_id = Column("id", Text, primary_key=True)
_connection = Column("connection", Text, nullable=False)
_state = Column("state", Text, nullable=False)
_record = Column("record", JSONB, nullable=False)


class CartOperationRow(SQLModel, table=True):
    # SQLModel accepts a string here; its inherited annotation also describes a descriptor.
    __tablename__: ClassVar[Any] = "cart_operations"

    __table_args__ = (
        CheckConstraint(
            _state.in_(("prepared", "executing", "uncertain", "applied", "rejected")),
            name="cart_operations_state_check",
        ),
        CheckConstraint(_record["state"].astext == _state, name="cart_operations_check"),
        CheckConstraint(
            _record["connection_id"].astext == _connection, name="cart_operations_check1"
        ),
        CheckConstraint(_record["plan"]["plan_id"].astext == _id, name="cart_operations_check2"),
        Index(
            "cart_one_unresolved_operation",
            _connection,
            unique=True,
            postgresql_where=_state.in_(("executing", "uncertain")),
        ),
    )

    id: str = Field(sa_column=_id)
    connection: str = Field(sa_column=_connection)
    state: OperationState = Field(sa_column=_state)
    revision: int = Field(
        default=0, sa_column=Column(BigInteger, nullable=False, server_default="0")
    )
    record: dict = Field(sa_column=_record)
    updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )

    def versioned_record(self) -> dict:
        return {**deepcopy(self.record), "_revision": self.revision}
