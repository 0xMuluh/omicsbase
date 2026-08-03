"""Add durable artifacts produced by NoteThread executions.

Revision ID: 0009_note_execution_artifacts
Revises: 0008_note_execution_controls
Create Date: 2026-08-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0009_note_execution_artifacts"
down_revision = "0008_note_execution_controls"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _index_names(table_name: str) -> set[str]:
    return {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes(table_name)
    }


def upgrade() -> None:
    if "note_execution_artifacts" not in _table_names():
        op.create_table(
            "note_execution_artifacts",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("execution_id", sa.String(length=36), nullable=False),
            sa.Column("artifact_type", sa.String(length=32), nullable=False, server_default="console"),
            sa.Column("relative_path", sa.String(length=1000), nullable=False),
            sa.Column("mime_type", sa.String(length=160), nullable=False, server_default="text/plain"),
            sa.Column("byte_size", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("sha256", sa.String(length=128), nullable=False),
            sa.Column("metadata", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["execution_id"], ["note_cell_executions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "execution_id",
                "artifact_type",
                "relative_path",
                name="uq_note_execution_artifact_path",
            ),
        )

    if "note_execution_artifacts" in _table_names():
        existing = _index_names("note_execution_artifacts")
        if "ix_note_execution_artifacts_execution_id" not in existing:
            op.create_index(
                "ix_note_execution_artifacts_execution_id",
                "note_execution_artifacts",
                ["execution_id"],
            )
        if "ix_note_execution_artifacts_execution_created" not in existing:
            op.create_index(
                "ix_note_execution_artifacts_execution_created",
                "note_execution_artifacts",
                ["execution_id", "created_at"],
            )


def downgrade() -> None:
    if "note_execution_artifacts" not in _table_names():
        return
    existing = _index_names("note_execution_artifacts")
    for index_name in (
        "ix_note_execution_artifacts_execution_created",
        "ix_note_execution_artifacts_execution_id",
    ):
        if index_name in existing:
            op.drop_index(index_name, table_name="note_execution_artifacts")
    op.drop_table("note_execution_artifacts")
