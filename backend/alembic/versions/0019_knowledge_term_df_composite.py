"""Composite index for fast per-snapshot term frequency aggregation.

Revision ID: 0019_knowledge_term_df_composite
Revises: 0018_knowledge_term_df
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0019_knowledge_term_df_composite"
down_revision: Union[str, None] = "0018_knowledge_term_df"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_bioc_knowledge_term_df_snapshot_term",
        "bioc_knowledge_term_df",
        ["snapshot_id", "term"],
    )


def downgrade() -> None:
    op.drop_index("ix_bioc_knowledge_term_df_snapshot_term", table_name="bioc_knowledge_term_df")
