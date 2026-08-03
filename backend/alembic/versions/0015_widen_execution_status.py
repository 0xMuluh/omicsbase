"""Widen note cell execution status to hold completed_with_errors.

Revision ID: 0015_widen_execution_status
Revises: 0014_shared_agent_runs
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0015_widen_execution_status"
down_revision: Union[str, None] = "0014_shared_agent_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "note_cell_executions",
        "status",
        existing_type=sa.String(length=20),
        type_=sa.String(length=32),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "note_cell_executions",
        "status",
        existing_type=sa.String(length=32),
        type_=sa.String(length=20),
        existing_nullable=False,
    )
