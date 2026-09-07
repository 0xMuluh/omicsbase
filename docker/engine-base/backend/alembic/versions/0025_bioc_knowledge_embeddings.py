"""Add versioned local embeddings for curated knowledge chunks.

Revision ID: 0025_bioc_knowledge_embeddings
Revises: 0024_agent_continuation_indexes
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0025_bioc_knowledge_embeddings"
down_revision: Union[str, None] = "0024_agent_continuation_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The application historically called Base.metadata.create_all() during
    # startup. Some local databases therefore already have this table while
    # Alembic still reports 0024. Reconcile that state without discarding data.
    bind = op.get_bind()
    table_name = "bioc_knowledge_embeddings"
    if table_name not in sa.inspect(bind).get_table_names():
        op.create_table(
            table_name,
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("chunk_id", sa.String(length=36), nullable=False),
            sa.Column("model_name", sa.String(length=255), nullable=False),
            sa.Column("dimension", sa.Integer(), nullable=False),
            sa.Column("vector", sa.LargeBinary(), nullable=False),
            sa.Column("content_sha256", sa.String(length=128), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["chunk_id"], ["bioc_knowledge_chunks.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("chunk_id", "model_name", name="uq_bioc_embedding_chunk_model"),
        )

    index_names = {
        index["name"]
        for index in sa.inspect(bind).get_indexes(table_name)
    }
    if "ix_bioc_knowledge_embeddings_chunk_id" not in index_names:
        op.create_index(
            "ix_bioc_knowledge_embeddings_chunk_id",
            table_name,
            ["chunk_id"],
        )
    if "ix_bioc_embeddings_model_chunk" not in index_names:
        op.create_index(
            "ix_bioc_embeddings_model_chunk",
            table_name,
            ["model_name", "chunk_id"],
        )


def downgrade() -> None:
    op.drop_index("ix_bioc_embeddings_model_chunk", table_name="bioc_knowledge_embeddings")
    op.drop_index("ix_bioc_knowledge_embeddings_chunk_id", table_name="bioc_knowledge_embeddings")
    op.drop_table("bioc_knowledge_embeddings")
