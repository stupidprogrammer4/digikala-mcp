"""Baseline PostgreSQL cart operation journal.

Revision ID: 0001_cart_journal
Revises: None
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_cart_journal"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cart_operations",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("connection", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("record", postgresql.JSONB(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "state IN ('prepared', 'executing', 'uncertain', 'applied', 'rejected')",
            name="cart_operations_state_check",
        ),
        sa.CheckConstraint("record->>'state' = state", name="cart_operations_check"),
        sa.CheckConstraint("record->>'connection_id' = connection", name="cart_operations_check1"),
        sa.CheckConstraint("record->'plan'->>'plan_id' = id", name="cart_operations_check2"),
    )
    op.create_index(
        "cart_one_unresolved_operation",
        "cart_operations",
        ["connection"],
        unique=True,
        postgresql_where=sa.text("state IN ('executing', 'uncertain')"),
    )


def downgrade() -> None:
    op.drop_index("cart_one_unresolved_operation", table_name="cart_operations")
    op.drop_table("cart_operations")
