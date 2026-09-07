"""Add durable timeout and cancellation controls to note cell executions.

Revision ID: 0008_note_execution_controls
Revises: 0007_note_domain
Create Date: 2026-08-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0008_note_execution_controls"
down_revision = "0007_note_domain"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def upgrade() -> None:
    if "timeout_seconds" not in _columns("note_cell_executions"):
        op.add_column(
            "note_cell_executions",
            sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="120"),
        )

    if "cancel_requested" not in _columns("note_cell_executions"):
        op.add_column(
            "note_cell_executions",
            sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    if "cancel_requested" in _columns("note_cell_executions"):
        op.drop_column("note_cell_executions", "cancel_requested")
    if "timeout_seconds" in _columns("note_cell_executions"):
        op.drop_column("note_cell_executions", "timeout_seconds")

