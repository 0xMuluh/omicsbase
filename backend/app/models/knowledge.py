"""Durable metadata for QMD-first Bioconductor book knowledge."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class BiocBookSource(Base):
    """Curated identity and synchronization policy for one online book."""

    __tablename__ = "bioc_book_sources"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    slug = Column(String(120), nullable=False, unique=True)
    title = Column(String(255), nullable=False)
    description = Column(Text)
    book_url = Column(String(1000))
    repository_url = Column(String(1000))
    license = Column(String(255))
    stable_ref = Column(String(255), nullable=False, default="release")
    preview_ref = Column(String(255), nullable=False, default="devel")
    enabled = Column(Boolean, nullable=False, default=True)
    source_metadata = Column("metadata", JSON)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    snapshots = relationship(
        "BiocBookSnapshot",
        back_populates="source",
        cascade="all, delete-orphan",
        order_by="BiocBookSnapshot.updated_at.desc()",
    )
    sync_runs = relationship(
        "BiocKnowledgeSyncRun",
        back_populates="source",
        cascade="all, delete-orphan",
        order_by="BiocKnowledgeSyncRun.started_at.desc()",
    )


class BiocBookSnapshot(Base):
    """Immutable content snapshot for a stable or preview book channel."""

    __tablename__ = "bioc_book_snapshots"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_id = Column(String(36), ForeignKey("bioc_book_sources.id", ondelete="CASCADE"), nullable=False, index=True)
    channel = Column(String(20), nullable=False, default="stable")
    requested_ref = Column(String(255), nullable=False)
    snapshot_key = Column(String(128), nullable=False)
    commit_sha = Column(String(128), nullable=False)
    status = Column(String(20), nullable=False, default="staged")
    document_count = Column(Integer, nullable=False, default=0)
    chunk_count = Column(Integer, nullable=False, default=0)
    snapshot_metadata = Column("metadata", JSON)
    published_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    source = relationship("BiocBookSource", back_populates="snapshots")
    documents = relationship(
        "BiocBookDocument",
        back_populates="snapshot",
        cascade="all, delete-orphan",
        order_by="BiocBookDocument.relative_path",
    )

    __table_args__ = (
        UniqueConstraint("source_id", "channel", "snapshot_key", name="uq_bioc_snapshot_identity"),
        Index("ix_bioc_snapshots_channel_status", "channel", "status"),
    )


class BiocBookDocument(Base):
    """One source QMD file within an immutable book snapshot."""

    __tablename__ = "bioc_book_documents"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    snapshot_id = Column(String(36), ForeignKey("bioc_book_snapshots.id", ondelete="CASCADE"), nullable=False, index=True)
    relative_path = Column(String(1000), nullable=False)
    title = Column(String(255), nullable=False)
    content_sha256 = Column(String(128), nullable=False)
    frontmatter = Column(JSON)
    source_url = Column(String(1500))
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    snapshot = relationship("BiocBookSnapshot", back_populates="documents")
    chunks = relationship(
        "BiocKnowledgeChunk",
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="BiocKnowledgeChunk.ordinal",
    )

    __table_args__ = (
        UniqueConstraint("snapshot_id", "relative_path", name="uq_bioc_document_path"),
        Index("ix_bioc_documents_snapshot_title", "snapshot_id", "title"),
    )


class BiocKnowledgeChunk(Base):
    """Retrieval unit retaining prose, code, and section provenance."""

    __tablename__ = "bioc_knowledge_chunks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String(36), ForeignKey("bioc_book_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    ordinal = Column(Integer, nullable=False)
    chunk_type = Column(String(20), nullable=False, default="prose")
    heading_path = Column(JSON)
    prose = Column(Text, nullable=False, default="")
    code = Column(Text)
    code_language = Column(String(32))
    content = Column(Text, nullable=False)
    search_text = Column(Text, nullable=False)
    content_sha256 = Column(String(128), nullable=False)
    source_start_line = Column(Integer)
    source_end_line = Column(Integer)
    chunk_metadata = Column("metadata", JSON)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    document = relationship("BiocBookDocument", back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_bioc_chunk_ordinal"),
        Index("ix_bioc_chunks_document_ordinal", "document_id", "ordinal"),
    )


class BiocKnowledgeTermDf(Base):
    """Precomputed per-snapshot term document frequencies for fast retrieval.

    Built at sync time from chunk ``search_text`` tokens; the search path sums
    these counts over the published snapshots of the requested channel so it no
    longer has to load every chunk's text to rank candidates.
    """

    __tablename__ = "bioc_knowledge_term_df"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    snapshot_id = Column(
        String(36),
        ForeignKey("bioc_book_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    term = Column(String(128), nullable=False)
    doc_count = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("snapshot_id", "term", name="uq_bioc_knowledge_term_df"),
    )


class BiocKnowledgeSyncRun(Base):
    """Audit record for a scheduled or manual knowledge synchronization."""

    __tablename__ = "bioc_knowledge_sync_runs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_id = Column(String(36), ForeignKey("bioc_book_sources.id", ondelete="CASCADE"), nullable=False, index=True)
    channel = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, default="queued")
    requested_ref = Column(String(255))
    resolved_ref = Column(String(128))
    files_seen = Column(Integer, nullable=False, default=0)
    documents_indexed = Column(Integer, nullable=False, default=0)
    chunks_indexed = Column(Integer, nullable=False, default=0)
    error = Column(Text)
    run_metadata = Column("metadata", JSON)
    started_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    finished_at = Column(DateTime(timezone=True))

    source = relationship("BiocBookSource", back_populates="sync_runs")

    __table_args__ = (
        Index("ix_bioc_sync_runs_source_started", "source_id", "started_at"),
        Index("ix_bioc_sync_runs_status_started", "status", "started_at"),
    )

