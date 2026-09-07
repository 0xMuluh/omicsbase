"""Add a pg_trgm GIN index for fast substring candidates over chunk search text.

Revision ID: 0017_knowledge_search_index
Revises: 0016_widen_event_status
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0017_knowledge_search_index"
down_revision: Union[str, None] = "0016_widen_event_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bioc_knowledge_chunks_search_text_trgm "
        "ON bioc_knowledge_chunks USING gin (search_text gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_bioc_knowledge_chunks_search_text_trgm")
