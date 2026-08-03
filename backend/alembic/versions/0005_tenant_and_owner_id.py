"""Add owner_id and tenant_id columns to projects table.

Revision ID: 0005_tenant_and_owner_id
Revises: 0004_project_messages
Create Date: 2026-07-31
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_tenant_and_owner_id"
down_revision = "0004_project_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col["name"] for col in inspector.get_columns("projects")]

    if "owner_id" not in columns:
        op.add_column(
            "projects",
            sa.Column("owner_id", sa.String(length=100), nullable=False, server_default="default_user"),
        )
        op.create_index("ix_projects_owner_id", "projects", ["owner_id"])

    if "tenant_id" not in columns:
        op.add_column(
            "projects",
            sa.Column("tenant_id", sa.String(length=100), nullable=False, server_default="default_tenant"),
        )
        op.create_index("ix_projects_tenant_id", "projects", ["tenant_id"])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col["name"] for col in inspector.get_columns("projects")]

    if "tenant_id" in columns:
        op.drop_index("ix_projects_tenant_id", table_name="projects")
        op.drop_column("projects", "tenant_id")

    if "owner_id" in columns:
        op.drop_index("ix_projects_owner_id", table_name="projects")
        op.drop_column("projects", "owner_id")
