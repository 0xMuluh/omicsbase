"""Add durable idempotency keys for Note cell execution requests.

Revision ID: 0023_note_execution_idempotency
Revises: 0022_project_edits
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0023_note_execution_idempotency"
down_revision: Union[str, None] = "0022_project_edits"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Batch mode keeps this migration usable by the repository's SQLite test
    # database as well as PostgreSQL (where it emits ordinary ALTER TABLE).
    with op.batch_alter_table("note_cell_executions") as batch_op:
        batch_op.add_column(
            sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        )
        batch_op.create_unique_constraint(
            "uq_note_cell_execution_idempotency",
            ["revision_id", "idempotency_key"],
        )


def downgrade() -> None:
    with op.batch_alter_table("note_cell_executions") as batch_op:
        batch_op.drop_constraint(
            "uq_note_cell_execution_idempotency",
            type_="unique",
        )
        batch_op.drop_column("idempotency_key")
