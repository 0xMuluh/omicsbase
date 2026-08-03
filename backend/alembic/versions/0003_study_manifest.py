"""Add canonical study manifest and first-class custom plan.

Revision ID: 0003_study_manifest
Revises: 0002_agent_runtime_columns
Create Date: 2026-07-30
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_study_manifest"
down_revision = "0002_agent_runtime_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("custom_plan_text", sa.Text(), nullable=True))
    op.add_column("projects", sa.Column("study_manifest", sa.JSON(), nullable=True))
    op.add_column(
        "projects",
        sa.Column("auto_build", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("projects", "auto_build")
    op.drop_column("projects", "study_manifest")
    op.drop_column("projects", "custom_plan_text")
