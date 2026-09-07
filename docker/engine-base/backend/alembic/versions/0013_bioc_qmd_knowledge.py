"""Add durable QMD-first Bioconductor knowledge snapshots.

Revision ID: 0013_bioc_qmd_knowledge
Revises: 0012_standalone_note_threads
Create Date: 2026-08-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0013_bioc_qmd_knowledge"
down_revision = "0012_standalone_note_threads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The application historically called Base.metadata.create_all at startup.
    # If that has already materialised this complete migration, let Alembic
    # record the revision instead of failing on duplicate tables.
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    knowledge_tables = {
        "bioc_book_sources",
        "bioc_book_snapshots",
        "bioc_book_documents",
        "bioc_knowledge_chunks",
        "bioc_knowledge_sync_runs",
    }
    if knowledge_tables.issubset(existing_tables):
        return
    op.create_table(
        "bioc_book_sources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("book_url", sa.String(length=1000), nullable=True),
        sa.Column("repository_url", sa.String(length=1000), nullable=True),
        sa.Column("license", sa.String(length=255), nullable=True),
        sa.Column("stable_ref", sa.String(length=255), nullable=False, server_default="release"),
        sa.Column("preview_ref", sa.String(length=255), nullable=False, server_default="devel"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index("ix_bioc_book_sources_slug", "bioc_book_sources", ["slug"], unique=True)

    op.create_table(
        "bioc_book_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False, server_default="stable"),
        sa.Column("requested_ref", sa.String(length=255), nullable=False),
        sa.Column("snapshot_key", sa.String(length=128), nullable=False),
        sa.Column("commit_sha", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="staged"),
        sa.Column("document_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["bioc_book_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "channel", "snapshot_key", name="uq_bioc_snapshot_identity"),
    )
    op.create_index("ix_bioc_book_snapshots_source_id", "bioc_book_snapshots", ["source_id"])
    op.create_index("ix_bioc_snapshots_channel_status", "bioc_book_snapshots", ["channel", "status"])

    op.create_table(
        "bioc_book_documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("relative_path", sa.String(length=1000), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("content_sha256", sa.String(length=128), nullable=False),
        sa.Column("frontmatter", sa.JSON(), nullable=True),
        sa.Column("source_url", sa.String(length=1500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["bioc_book_snapshots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_id", "relative_path", name="uq_bioc_document_path"),
    )
    op.create_index("ix_bioc_book_documents_snapshot_id", "bioc_book_documents", ["snapshot_id"])
    op.create_index("ix_bioc_documents_snapshot_title", "bioc_book_documents", ["snapshot_id", "title"])

    op.create_table(
        "bioc_knowledge_chunks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("chunk_type", sa.String(length=20), nullable=False, server_default="prose"),
        sa.Column("heading_path", sa.JSON(), nullable=True),
        sa.Column("prose", sa.Text(), nullable=False, server_default=""),
        sa.Column("code", sa.Text(), nullable=True),
        sa.Column("code_language", sa.String(length=32), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=128), nullable=False),
        sa.Column("source_start_line", sa.Integer(), nullable=True),
        sa.Column("source_end_line", sa.Integer(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["bioc_book_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "ordinal", name="uq_bioc_chunk_ordinal"),
    )
    op.create_index("ix_bioc_knowledge_chunks_document_id", "bioc_knowledge_chunks", ["document_id"])
    op.create_index("ix_bioc_chunks_document_ordinal", "bioc_knowledge_chunks", ["document_id", "ordinal"])

    op.create_table(
        "bioc_knowledge_sync_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("requested_ref", sa.String(length=255), nullable=True),
        sa.Column("resolved_ref", sa.String(length=128), nullable=True),
        sa.Column("files_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("documents_indexed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunks_indexed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["source_id"], ["bioc_book_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bioc_knowledge_sync_runs_source_id", "bioc_knowledge_sync_runs", ["source_id"])
    op.create_index("ix_bioc_sync_runs_source_started", "bioc_knowledge_sync_runs", ["source_id", "started_at"])
    op.create_index("ix_bioc_sync_runs_status_started", "bioc_knowledge_sync_runs", ["status", "started_at"])


def downgrade() -> None:
    op.drop_index("ix_bioc_sync_runs_status_started", table_name="bioc_knowledge_sync_runs")
    op.drop_index("ix_bioc_sync_runs_source_started", table_name="bioc_knowledge_sync_runs")
    op.drop_index("ix_bioc_knowledge_sync_runs_source_id", table_name="bioc_knowledge_sync_runs")
    op.drop_table("bioc_knowledge_sync_runs")
    op.drop_index("ix_bioc_chunks_document_ordinal", table_name="bioc_knowledge_chunks")
    op.drop_index("ix_bioc_knowledge_chunks_document_id", table_name="bioc_knowledge_chunks")
    op.drop_table("bioc_knowledge_chunks")
    op.drop_index("ix_bioc_documents_snapshot_title", table_name="bioc_book_documents")
    op.drop_index("ix_bioc_book_documents_snapshot_id", table_name="bioc_book_documents")
    op.drop_table("bioc_book_documents")
    op.drop_index("ix_bioc_snapshots_channel_status", table_name="bioc_book_snapshots")
    op.drop_index("ix_bioc_book_snapshots_source_id", table_name="bioc_book_snapshots")
    op.drop_table("bioc_book_snapshots")
    op.drop_index("ix_bioc_book_sources_slug", table_name="bioc_book_sources")
    op.drop_table("bioc_book_sources")
