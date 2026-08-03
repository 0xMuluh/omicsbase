"""Add replayable lifecycle events for NoteThread executions.

Revision ID: 0010_note_execution_events
Revises: 0009_note_execution_artifacts
Create Date: 2026-08-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0010_note_execution_events"
down_revision = "0009_note_execution_artifacts"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _index_names(table_name: str) -> set[str]:
    return {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes(table_name)
    }


def upgrade() -> None:
    if "event_sequence" not in _columns("note_cell_executions"):
        op.add_column(
            "note_cell_executions",
            sa.Column("event_sequence", sa.Integer(), nullable=False, server_default="0"),
        )

    if "note_execution_events" not in _table_names():
        op.create_table(
            "note_execution_events",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("execution_id", sa.String(length=36), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(length=48), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["execution_id"], ["note_cell_executions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "execution_id",
                "sequence",
                name="uq_note_execution_event_sequence",
            ),
        )

    if "note_execution_events" in _table_names():
        existing = _index_names("note_execution_events")
        if "ix_note_execution_events_execution_id" not in existing:
            op.create_index(
                "ix_note_execution_events_execution_id",
                "note_execution_events",
                ["execution_id"],
            )
        if "ix_note_execution_events_execution_sequence" not in existing:
            op.create_index(
                "ix_note_execution_events_execution_sequence",
                "note_execution_events",
                ["execution_id", "sequence"],
            )


def downgrade() -> None:
    if "note_execution_events" in _table_names():
        existing = _index_names("note_execution_events")
        for index_name in (
            "ix_note_execution_events_execution_sequence",
            "ix_note_execution_events_execution_id",
        ):
            if index_name in existing:
                op.drop_index(index_name, table_name="note_execution_events")
        op.drop_table("note_execution_events")
    if "event_sequence" in _columns("note_cell_executions"):
        op.drop_column("note_cell_executions", "event_sequence")
