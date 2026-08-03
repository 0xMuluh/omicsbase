"""Persist workspace conversation and agent events.

Revision ID: 0004_project_messages
Revises: 0003_study_manifest
Create Date: 2026-07-30
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_project_messages"
down_revision = "0003_study_manifest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False, server_default="message"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_project_messages_project_id",
        "project_messages",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_project_messages_project_id", table_name="project_messages")
    op.drop_table("project_messages")
