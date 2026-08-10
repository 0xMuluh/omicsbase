"""Shared durable orchestration records for Workspace and NoteThread agents."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
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


class AgentRun(Base):
    """One durable agent turn shared by the Workspace and NoteThread surfaces.

    A run is the orchestration envelope, not the scientific workspace and not
    a notebook cell. It can point at either surface, retains the request
    fingerprint used for idempotency, and owns the append-only event stream.
    """

    __tablename__ = "agent_runs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(100), nullable=False, index=True)
    owner_id = Column(String(100), nullable=False, index=True)
    surface = Column(String(32), nullable=False)
    kind = Column(String(32), nullable=False, default="agent_turn")
    project_id = Column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    note_thread_id = Column(
        String(36),
        ForeignKey("note_threads.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    status = Column(String(32), nullable=False, default="queued")
    idempotency_scope = Column(String(255), nullable=False)
    idempotency_key = Column(String(255), nullable=False)
    request_hash = Column(String(128), nullable=False)
    input_payload = Column(JSON)
    result_payload = Column(JSON)
    run_metadata = Column("metadata", JSON)
    continuation_status = Column(String(32), nullable=True, index=True)
    continuation_dependency_kind = Column(String(40), nullable=True)
    continuation_dependency_id = Column(String(128), nullable=True)
    continuation_attempts = Column(Integer, nullable=False, default=0)
    current_step = Column(Integer, nullable=False, default=0)
    event_sequence = Column(Integer, nullable=False, default=0)
    cancel_requested = Column(Boolean, nullable=False, default=False)
    resumable = Column(Boolean, nullable=False, default=True)
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    heartbeat_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    events = relationship(
        "RunEvent",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="RunEvent.sequence",
    )
    telemetry = relationship(
        "RunTelemetry",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="RunTelemetry.created_at",
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "idempotency_scope",
            "idempotency_key",
            name="uq_agent_run_idempotency",
        ),
        Index("ix_agent_runs_surface_status_created", "surface", "status", "created_at"),
        Index("ix_agent_runs_tenant_created", "tenant_id", "created_at"),
        Index(
            "ix_agent_runs_continuation_dependency",
            "continuation_status",
            "continuation_dependency_kind",
            "continuation_dependency_id",
        ),
        CheckConstraint(
            "surface IN ('workspace', 'notes')",
            name="ck_agent_run_surface",
        ),
    )


class RunEvent(Base):
    """Append-only, ordered event in an AgentRun event log."""

    __tablename__ = "agent_run_events"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id = Column(
        String(36),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence = Column(Integer, nullable=False)
    event_type = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False)
    idempotency_key = Column(String(255))
    event_payload = Column("payload", JSON)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    run = relationship("AgentRun", back_populates="events")

    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_agent_run_event_sequence"),
        UniqueConstraint("run_id", "idempotency_key", name="uq_agent_run_event_idempotency"),
        Index("ix_agent_run_events_run_sequence", "run_id", "sequence"),
    )


class RunTelemetry(Base):
    """Durable latency, usage, cost, and failure telemetry for one run."""

    __tablename__ = "agent_run_telemetry"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id = Column(
        String(36),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind = Column(String(32), nullable=False)
    operation = Column(String(128), nullable=False)
    provider = Column(String(64))
    model = Column(String(255))
    status = Column(String(32), nullable=False, default="completed")
    duration_ms = Column(Float)
    input_tokens = Column(Integer)
    output_tokens = Column(Integer)
    total_tokens = Column(Integer)
    cost_usd = Column(Float)
    error = Column(Text)
    telemetry_metadata = Column("metadata", JSON)
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    run = relationship("AgentRun", back_populates="telemetry")

    __table_args__ = (
        Index("ix_agent_run_telemetry_run_created", "run_id", "created_at"),
        Index("ix_agent_run_telemetry_kind_created", "kind", "created_at"),
    )

