"""Precompute per-snapshot term document frequencies for knowledge retrieval.

Revision ID: 0018_knowledge_term_df
Revises: 0017_knowledge_search_index
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0018_knowledge_term_df"
down_revision: Union[str, None] = "0017_knowledge_search_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    if "bioc_knowledge_term_df" not in existing:
        op.create_table(
            "bioc_knowledge_term_df",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("snapshot_id", sa.String(length=36), nullable=False),
            sa.Column("term", sa.String(length=128), nullable=False),
            sa.Column("doc_count", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["snapshot_id"], ["bioc_book_snapshots.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("snapshot_id", "term", name="uq_bioc_knowledge_term_df"),
        )
        op.create_index("ix_bioc_knowledge_term_df_snapshot_id", "bioc_knowledge_term_df", ["snapshot_id"])
    op.execute(
        """
        INSERT INTO bioc_knowledge_term_df (id, snapshot_id, term, doc_count)
        SELECT gen_random_uuid(), sub.snapshot_id, sub.term, count(DISTINCT sub.chunk_id)
        FROM (
            SELECT d.snapshot_id AS snapshot_id, c.id AS chunk_id, left(trim(token), 128) AS term
            FROM bioc_knowledge_chunks c
            JOIN bioc_book_documents d ON d.id = c.document_id
            CROSS JOIN LATERAL unnest(string_to_array(c.search_text, ' ')) AS token
            WHERE c.search_text <> ''
        ) sub
        WHERE sub.term <> ''
        GROUP BY sub.snapshot_id, sub.term
        ON CONFLICT (snapshot_id, term) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_bioc_knowledge_term_df_snapshot_id", table_name="bioc_knowledge_term_df")
    op.drop_table("bioc_knowledge_term_df")
