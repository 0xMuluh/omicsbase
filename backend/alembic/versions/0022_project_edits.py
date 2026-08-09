"""Add a durable index for filesystem edit transactions.

Revision ID: 0022_project_edits
Revises: 0021_project_name_source
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0022_project_edits"
down_revision: Union[str, None] = "0021_project_name_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_edits",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("transaction_id", sa.String(length=64), nullable=False),
        sa.Column("origin", sa.String(length=50), nullable=False, server_default=sa.text("'agent'")),
        sa.Column("summary", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'committed'")),
        sa.Column("files", sa.JSON(), nullable=False),
        sa.Column("diagnostics", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reverted_by", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("transaction_id", name="uq_project_edits_transaction_id"),
    )
    op.create_index("ix_project_edits_project_id", "project_edits", ["project_id"])
    op.create_index("ix_project_edits_project_created", "project_edits", ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_project_edits_project_created", table_name="project_edits")
    op.drop_index("ix_project_edits_project_id", table_name="project_edits")
    op.drop_table("project_edits")
