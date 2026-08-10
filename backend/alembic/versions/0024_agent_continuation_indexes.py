"""Add indexed fields for durable agent continuation lookup.

Revision ID: 0024_agent_continuation_indexes
Revises: 0023_note_execution_idempotency
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0024_agent_continuation_indexes"
down_revision: Union[str, None] = "0023_note_execution_idempotency"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.add_column(sa.Column("continuation_status", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("continuation_dependency_kind", sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column("continuation_dependency_id", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("continuation_attempts", sa.Integer(), nullable=False, server_default="0"))
        batch_op.create_index("ix_agent_runs_continuation_status", ["continuation_status"])
        batch_op.create_index(
            "ix_agent_runs_continuation_dependency",
            ["continuation_status", "continuation_dependency_kind", "continuation_dependency_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.drop_index("ix_agent_runs_continuation_dependency")
        batch_op.drop_index("ix_agent_runs_continuation_status")
        batch_op.drop_column("continuation_attempts")
        batch_op.drop_column("continuation_dependency_id")
        batch_op.drop_column("continuation_dependency_kind")
        batch_op.drop_column("continuation_status")
