"""Add optional typed cell metadata for the NoteThread compatibility layer.

Revision ID: 0006_message_cells
Revises: 0005_tenant_and_owner_id
Create Date: 2026-08-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006_message_cells"
down_revision = "0005_tenant_and_owner_id"
branch_labels = None
depends_on = None


_CELL_COLUMNS = {
    "cell_id": sa.String(length=36),
    "cell_type": sa.String(length=32),
    "cell_revision": sa.Integer(),
    "execution_id": sa.String(length=36),
}
_CELL_INDEXES = {
    "ix_project_messages_cell_id": ["cell_id"],
    "ix_project_messages_execution_id": ["execution_id"],
}


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = {column["name"] for column in inspector.get_columns("project_messages")}
    for name, column_type in _CELL_COLUMNS.items():
        if name not in columns:
            op.add_column(
                "project_messages",
                sa.Column(name, column_type, nullable=True),
            )

    indexes = {index["name"] for index in inspector.get_indexes("project_messages")}
    for index_name, fields in _CELL_INDEXES.items():
        if index_name not in indexes:
            op.create_index(index_name, "project_messages", fields)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    indexes = {index["name"] for index in inspector.get_indexes("project_messages")}
    for index_name in _CELL_INDEXES:
        if index_name in indexes:
            op.drop_index(index_name, table_name="project_messages")

    columns = {column["name"] for column in inspector.get_columns("project_messages")}
    for name in reversed(tuple(_CELL_COLUMNS)):
        if name in columns:
            op.drop_column("project_messages", name)

