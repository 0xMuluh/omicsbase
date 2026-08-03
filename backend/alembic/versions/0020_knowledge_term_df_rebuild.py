"""Rebuild term document frequencies with a whitespace split.

The original backfill produced unsplit terms; search_text is space-joined
tokens, so the split is simply ``string_to_array(search_text, ' ')``.

Revision ID: 0020_knowledge_term_df_rebuild
Revises: 0019_knowledge_term_df_composite
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0020_knowledge_term_df_rebuild"
down_revision: Union[str, None] = "0019_knowledge_term_df_composite"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DELETE FROM bioc_knowledge_term_df")
    op.execute(
        """
        INSERT INTO bioc_knowledge_term_df (id, snapshot_id, term, doc_count)
        SELECT gen_random_uuid(), sub.snapshot_id, sub.term, count(DISTINCT sub.chunk_id)
        FROM (
            SELECT d.snapshot_id AS snapshot_id, c.id AS chunk_id, trim(token) AS term
            FROM bioc_knowledge_chunks c
            JOIN bioc_book_documents d ON d.id = c.document_id
            CROSS JOIN LATERAL unnest(string_to_array(c.search_text, ' ')) AS token
            WHERE c.search_text <> ''
        ) sub
        WHERE sub.term <> ''
        GROUP BY sub.snapshot_id, sub.term
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM bioc_knowledge_term_df")
