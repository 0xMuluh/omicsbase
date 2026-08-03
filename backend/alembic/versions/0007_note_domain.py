"""Create separate NoteThread/cell execution and Report domain tables.

Revision ID: 0007_note_domain
Revises: 0006_message_cells
Create Date: 2026-08-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007_note_domain"
down_revision = "0006_message_cells"
branch_labels = None
depends_on = None


_INDEXES = {
    "note_threads": {
        "ix_note_threads_project_id": ["project_id"],
        "ix_note_threads_project_created": ["project_id", "created_at"],
    },
    "note_cells": {
        "ix_note_cells_thread_id": ["thread_id"],
        "ix_note_cells_thread_position": ["thread_id", "position"],
    },
    "note_cell_revisions": {
        "ix_note_cell_revisions_cell_id": ["cell_id"],
        "ix_note_cell_revisions_cell_revision": ["cell_id", "revision"],
    },
    "note_cell_executions": {
        "ix_note_cell_executions_revision_id": ["revision_id"],
        "ix_note_cell_executions_status_created": ["status", "created_at"],
    },
    "reports": {
        "ix_reports_project_id": ["project_id"],
        "ix_reports_project_created": ["project_id", "created_at"],
    },
}


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _index_names(table_name: str) -> set[str]:
    return {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes(table_name)
    }


def upgrade() -> None:
    tables = _table_names()

    if "note_threads" not in tables:
        op.create_table(
            "note_threads",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("project_id", sa.String(length=36), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False, server_default="Untitled note"),
            sa.Column("thread_type", sa.String(length=32), nullable=False, server_default="note"),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
            sa.Column("metadata", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    tables = _table_names()
    if "note_cells" not in tables:
        op.create_table(
            "note_cells",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("thread_id", sa.String(length=36), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["thread_id"], ["note_threads.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    tables = _table_names()
    if "note_cell_revisions" not in tables:
        op.create_table(
            "note_cell_revisions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("cell_id", sa.String(length=36), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("cell_type", sa.String(length=32), nullable=False),
            sa.Column("language", sa.String(length=32), nullable=True),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("metadata", sa.JSON(), nullable=True),
            sa.Column("created_by", sa.String(length=100), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["cell_id"], ["note_cells.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("cell_id", "revision", name="uq_note_cell_revision"),
            sa.CheckConstraint("revision > 0", name="ck_note_cell_revision_positive"),
        )

    tables = _table_names()
    if "note_cell_executions" not in tables:
        op.create_table(
            "note_cell_executions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("revision_id", sa.String(length=36), nullable=False),
            sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
            sa.Column("execution_kind", sa.String(length=20), nullable=False, server_default="isolated"),
            sa.Column("environment_fingerprint", sa.String(length=128), nullable=True),
            sa.Column("input_fingerprint", sa.String(length=128), nullable=True),
            sa.Column("parameters", sa.JSON(), nullable=True),
            sa.Column("result_metadata", sa.JSON(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["revision_id"], ["note_cell_revisions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("revision_id", "attempt", name="uq_note_cell_execution_attempt"),
        )

    tables = _table_names()
    if "reports" not in tables:
        op.create_table(
            "reports",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("project_id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("slug", sa.String(length=120), nullable=False),
            sa.Column("report_type", sa.String(length=32), nullable=False, server_default="quarto"),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
            sa.Column("source_path", sa.String(length=500), nullable=True),
            sa.Column("rendered_path", sa.String(length=500), nullable=True),
            sa.Column("metadata", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("project_id", "slug", name="uq_project_report_slug"),
        )

    for table_name, index_map in _INDEXES.items():
        if table_name not in _table_names():
            continue
        existing = _index_names(table_name)
        for index_name, columns in index_map.items():
            if index_name not in existing:
                op.create_index(index_name, table_name, columns)


def downgrade() -> None:
    for table_name, index_map in reversed(tuple(_INDEXES.items())):
        if table_name not in _table_names():
            continue
        existing = _index_names(table_name)
        for index_name in index_map:
            if index_name in existing:
                op.drop_index(index_name, table_name=table_name)

    tables = _table_names()
    for table_name in ("reports", "note_cell_executions", "note_cell_revisions", "note_cells", "note_threads"):
        if table_name in tables:
            op.drop_table(table_name)
            tables.remove(table_name)

