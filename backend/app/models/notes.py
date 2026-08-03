"""Durable NoteThread, cell revision, execution, and report domain models.

A Project is the existing workspace/container. These tables intentionally keep
interactive notes and published Quarto reports separate from that container.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class NoteThread(Base):
    """An interactive scientific notebook/conversation.

    A thread may begin as a standalone Chat/Notes entry point and can later be
    attached to a Project workspace. ``project_id`` is therefore deliberately
    nullable; tenant and owner scope remain first-class so standalone threads
    are never anonymous.
    """

    __tablename__ = "note_threads"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    tenant_id = Column(String(100), nullable=False, default="default_tenant", index=True)
    owner_id = Column(String(100), nullable=False, default="default_user", index=True)
    storage_path = Column(String(500), nullable=True)
    title = Column(String(255), nullable=False, default="Untitled note")
    thread_type = Column(String(32), nullable=False, default="note")
    status = Column(String(20), nullable=False, default="active")
    thread_metadata = Column("metadata", JSON)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    project = relationship("Project", back_populates="note_threads")
    cells = relationship(
        "NoteCell",
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="(NoteCell.position, NoteCell.created_at)",
    )

    __table_args__ = (
        Index("ix_note_threads_project_created", "project_id", "created_at"),
        Index("ix_note_threads_tenant_created", "tenant_id", "created_at"),
    )


class NoteCell(Base):
    """Stable identity and ordering for one notebook cell."""

    __tablename__ = "note_cells"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    thread_id = Column(
        String(36),
        ForeignKey("note_threads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position = Column(Integer, nullable=False, default=0)
    status = Column(String(20), nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    thread = relationship("NoteThread", back_populates="cells")
    revisions = relationship(
        "NoteCellRevision",
        back_populates="cell",
        cascade="all, delete-orphan",
        order_by="NoteCellRevision.revision",
    )

    __table_args__ = (
        Index("ix_note_cells_thread_position", "thread_id", "position"),
    )


class NoteCellRevision(Base):
    """Immutable source revision for a cell.

    The application appends rows instead of updating them. The unique
    (cell_id, revision) constraint makes revision identity durable.
    """

    __tablename__ = "note_cell_revisions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    cell_id = Column(
        String(36),
        ForeignKey("note_cells.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    revision = Column(Integer, nullable=False)
    cell_type = Column(String(32), nullable=False)
    language = Column(String(32))
    content = Column(Text, nullable=False)
    revision_metadata = Column("metadata", JSON)
    created_by = Column(String(100))
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    cell = relationship("NoteCell", back_populates="revisions")
    executions = relationship(
        "CellExecution",
        back_populates="revision_record",
        cascade="all, delete-orphan",
        order_by="CellExecution.created_at",
    )

    __table_args__ = (
        UniqueConstraint("cell_id", "revision", name="uq_note_cell_revision"),
        CheckConstraint("revision > 0", name="ck_note_cell_revision_positive"),
        Index("ix_note_cell_revisions_cell_revision", "cell_id", "revision"),
    )


class CellExecution(Base):
    """One durable execution attempt for one immutable cell revision."""

    __tablename__ = "note_cell_executions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    revision_id = Column(
        String(36),
        ForeignKey("note_cell_revisions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt = Column(Integer, nullable=False, default=1)
    status = Column(String(32), nullable=False, default="queued")
    cancel_requested = Column(Boolean, nullable=False, default=False)
    execution_kind = Column(String(20), nullable=False, default="isolated")
    timeout_seconds = Column(Integer, nullable=False, default=120)
    environment_fingerprint = Column(String(128))
    input_fingerprint = Column(String(128))
    parameters = Column(JSON)
    result_metadata = Column(JSON)
    error = Column(Text)
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    event_sequence = Column(Integer, nullable=False, default=0)
    cache_policy = Column(String(16), nullable=False, default="off")
    cache_key = Column(String(128), index=True)
    dependency_fingerprint = Column(String(128))
    upstream_execution_ids = Column(JSON)
    cache_hit = Column(Boolean, nullable=False, default=False)
    cache_source_execution_id = Column(
        String(36),
        ForeignKey("note_cell_executions.id", ondelete="SET NULL"),
        index=True,
    )

    revision_record = relationship("NoteCellRevision", back_populates="executions")
    artifacts = relationship(
        "NoteExecutionArtifact",
        back_populates="execution",
        cascade="all, delete-orphan",
        order_by="NoteExecutionArtifact.created_at",
    )
    events = relationship(
        "NoteExecutionEvent",
        back_populates="execution",
        cascade="all, delete-orphan",
        order_by="NoteExecutionEvent.sequence",
    )

    __table_args__ = (
        UniqueConstraint("revision_id", "attempt", name="uq_note_cell_execution_attempt"),
        Index("ix_note_cell_executions_status_created", "status", "created_at"),
    )


class NoteExecutionArtifact(Base):
    """Durable file output produced by one immutable cell execution.

    Artifact bytes live in the thread's execution root (the linked workspace
    for legacy workspace threads, or thread-owned storage for standalone
    threads). This row is the authoritative provenance/index record.
    """

    __tablename__ = "note_execution_artifacts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    execution_id = Column(
        String(36),
        ForeignKey("note_cell_executions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    artifact_type = Column(String(32), nullable=False, default="console")
    relative_path = Column(String(1000), nullable=False)
    mime_type = Column(String(160), nullable=False, default="text/plain")
    byte_size = Column(Integer, nullable=False, default=0)
    sha256 = Column(String(128), nullable=False)
    artifact_metadata = Column("metadata", JSON)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    execution = relationship("CellExecution", back_populates="artifacts")

    __table_args__ = (
        UniqueConstraint(
            "execution_id",
            "artifact_type",
            "relative_path",
            name="uq_note_execution_artifact_path",
        ),
        Index(
            "ix_note_execution_artifacts_execution_created",
            "execution_id",
            "created_at",
        ),
    )


class NoteExecutionEvent(Base):
    """Append-only lifecycle event for reconnectable execution history."""

    __tablename__ = "note_execution_events"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    execution_id = Column(
        String(36),
        ForeignKey("note_cell_executions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence = Column(Integer, nullable=False)
    event_type = Column(String(48), nullable=False)
    status = Column(String(32), nullable=False)
    event_payload = Column("payload", JSON)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    execution = relationship("CellExecution", back_populates="events")

    __table_args__ = (
        UniqueConstraint(
            "execution_id",
            "sequence",
            name="uq_note_execution_event_sequence",
        ),
        Index(
            "ix_note_execution_events_execution_sequence",
            "execution_id",
            "sequence",
        ),
    )


class Report(Base):
    """A published/reporting surface inside a workspace, separate from notes."""

    __tablename__ = "reports"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(255), nullable=False)
    slug = Column(String(120), nullable=False)
    report_type = Column(String(32), nullable=False, default="quarto")
    status = Column(String(20), nullable=False, default="draft")
    source_path = Column(String(500))
    rendered_path = Column(String(500))
    report_metadata = Column("metadata", JSON)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    project = relationship("Project", back_populates="reports")

    __table_args__ = (
        UniqueConstraint("project_id", "slug", name="uq_project_report_slug"),
        Index("ix_reports_project_created", "project_id", "created_at"),
    )

