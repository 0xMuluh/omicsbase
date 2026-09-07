"""Add explicit cache and reproducibility metadata to NoteThread executions.

Revision ID: 0011_note_execution_cache
Revises: 0010_note_execution_events
Create Date: 2026-08-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0011_note_execution_cache"
down_revision = "0010_note_execution_events"
branch_labels = None
depends_on = None


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


def _foreign_key_names(table_name: str) -> set[str]:
    return {
        foreign_key.get("name")
        for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys(table_name)
    }


def upgrade() -> None:
    table_name = "note_cell_executions"
    columns = _columns(table_name)

    if "cache_policy" not in columns:
        op.add_column(
            table_name,
            sa.Column("cache_policy", sa.String(length=16), nullable=False, server_default="off"),
        )
    if "cache_key" not in columns:
        op.add_column(table_name, sa.Column("cache_key", sa.String(length=128), nullable=True))
    if "dependency_fingerprint" not in columns:
        op.add_column(
            table_name,
            sa.Column("dependency_fingerprint", sa.String(length=128), nullable=True),
        )
    if "upstream_execution_ids" not in columns:
        op.add_column(table_name, sa.Column("upstream_execution_ids", sa.JSON(), nullable=True))
    if "cache_hit" not in columns:
        op.add_column(
            table_name,
            sa.Column("cache_hit", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if "cache_source_execution_id" not in columns:
        op.add_column(
            table_name,
            sa.Column("cache_source_execution_id", sa.String(length=36), nullable=True),
        )

    foreign_key_name = "fk_note_cell_executions_cache_source"
    if foreign_key_name not in _foreign_key_names(table_name):
        op.create_foreign_key(
            foreign_key_name,
            table_name,
            table_name,
            ["cache_source_execution_id"],
            ["id"],
            ondelete="SET NULL",
        )

    indexes = _index_names(table_name)
    if "ix_note_cell_executions_cache_key" not in indexes:
        op.create_index(
            "ix_note_cell_executions_cache_key",
            table_name,
            ["cache_key"],
        )
    if "ix_note_cell_executions_cache_source_execution_id" not in indexes:
        op.create_index(
            "ix_note_cell_executions_cache_source_execution_id",
            table_name,
            ["cache_source_execution_id"],
        )


def downgrade() -> None:
    table_name = "note_cell_executions"
    if "ix_note_cell_executions_cache_source_execution_id" in _index_names(table_name):
        op.drop_index(
            "ix_note_cell_executions_cache_source_execution_id",
            table_name=table_name,
        )
    if "ix_note_cell_executions_cache_key" in _index_names(table_name):
        op.drop_index("ix_note_cell_executions_cache_key", table_name=table_name)

    foreign_key_name = "fk_note_cell_executions_cache_source"
    if foreign_key_name in _foreign_key_names(table_name):
        op.drop_constraint(foreign_key_name, table_name=table_name, type_="foreignkey")

    columns = _columns(table_name)
    for column_name in (
        "cache_source_execution_id",
        "cache_hit",
        "upstream_execution_ids",
        "dependency_fingerprint",
        "cache_key",
        "cache_policy",
    ):
        if column_name in columns:
            op.drop_column(table_name, column_name)

