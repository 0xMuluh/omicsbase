"""Add agent runtime columns.

Revision ID: 0002_agent_runtime_columns
Revises: 0001_initial_schema
Create Date: 2026-07-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_agent_runtime_columns"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("agent_state", sa.String(length=50), nullable=True, server_default="idle"))
    op.add_column("projects", sa.Column("agent_memory", sa.JSON(), nullable=True))
    op.add_column("projects", sa.Column("agent_actions", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "agent_actions")
    op.drop_column("projects", "agent_memory")
    op.drop_column("projects", "agent_state")
