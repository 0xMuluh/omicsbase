"""Allow standalone NoteThreads with direct tenant ownership and storage.

Revision ID: 0012_standalone_note_threads
Revises: 0011_note_execution_cache
Create Date: 2026-08-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0012_standalone_note_threads"
down_revision = "0011_note_execution_cache"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def upgrade() -> None:
    table_name = "note_threads"
    columns = _columns(table_name)

    if "tenant_id" not in columns:
        op.add_column(
            table_name,
            sa.Column("tenant_id", sa.String(length=100), nullable=False, server_default="default_tenant"),
        )
    if "owner_id" not in columns:
        op.add_column(
            table_name,
            sa.Column("owner_id", sa.String(length=100), nullable=False, server_default="default_user"),
        )
    if "storage_path" not in columns:
        op.add_column(table_name, sa.Column("storage_path", sa.String(length=500), nullable=True))

    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "sqlite":
        with op.batch_alter_table(table_name) as batch:
            batch.alter_column("project_id", existing_type=sa.String(length=36), nullable=True)
    else:
        op.alter_column(
            table_name,
            "project_id",
            existing_type=sa.String(length=36),
            nullable=True,
        )

    notes = sa.table(
        table_name,
        sa.column("id", sa.String(length=36)),
        sa.column("project_id", sa.String(length=36)),
        sa.column("tenant_id", sa.String(length=100)),
        sa.column("owner_id", sa.String(length=100)),
    )
    projects = sa.table(
        "projects",
        sa.column("id", sa.String(length=36)),
        sa.column("tenant_id", sa.String(length=100)),
        sa.column("owner_id", sa.String(length=100)),
    )
    rows = bind.execute(
        sa.select(notes.c.id, notes.c.project_id, projects.c.tenant_id, projects.c.owner_id)
        .select_from(notes.outerjoin(projects, notes.c.project_id == projects.c.id))
    ).fetchall()
    for row in rows:
        bind.execute(
            notes.update()
            .where(notes.c.id == row.id)
            .values(
                tenant_id=row.tenant_id or "default_tenant",
                owner_id=row.owner_id or "default_user",
            )
        )

    indexes = _index_names(table_name)
    if "ix_note_threads_tenant_id" not in indexes:
        op.create_index("ix_note_threads_tenant_id", table_name, ["tenant_id"])
    if "ix_note_threads_owner_id" not in indexes:
        op.create_index("ix_note_threads_owner_id", table_name, ["owner_id"])
    if "ix_note_threads_tenant_created" not in indexes:
        op.create_index("ix_note_threads_tenant_created", table_name, ["tenant_id", "created_at"])


def downgrade() -> None:
    table_name = "note_threads"
    indexes = _index_names(table_name)
    for index_name in (
        "ix_note_threads_tenant_created",
        "ix_note_threads_owner_id",
        "ix_note_threads_tenant_id",
    ):
        if index_name in indexes:
            op.drop_index(index_name, table_name=table_name)

    columns = _columns(table_name)
    for column_name in ("storage_path", "owner_id", "tenant_id"):
        if column_name in columns:
            op.drop_column(table_name, column_name)

    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(table_name) as batch:
            batch.alter_column("project_id", existing_type=sa.String(length=36), nullable=False)
    else:
        op.alter_column(
            table_name,
            "project_id",
            existing_type=sa.String(length=36),
            nullable=False,
        )
