"""SQLAlchemy ORM models for projects and related entities (SQLite and PostgreSQL compatible)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.database import Base


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint(
            "name_source IN ('default', 'auto', 'user')",
            name="ck_projects_name_source",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_id = Column(String(100), default="default_user", nullable=False, index=True)
    tenant_id = Column(String(100), default="default_tenant", nullable=False, index=True)
    name = Column(String(255), nullable=False)
    name_source = Column(String(20), default="default", server_default="default", nullable=False)
    question = Column(Text)
    notes = Column(Text)
    custom_plan_text = Column(Text)
    auto_build = Column(Boolean, default=True, nullable=False)
    status = Column(String(50), default="created")
    agent_state = Column(String(50), default="idle")
    agent_memory = Column(JSON)
    agent_actions = Column(JSON)
    study_manifest = Column(JSON)
    analysis_plan = Column(JSON)
    project_dir = Column(String(500))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    files = relationship("UploadedFile", back_populates="project", cascade="all, delete-orphan")
    jobs = relationship("Job", back_populates="project", cascade="all, delete-orphan")
    messages = relationship(
        "ProjectMessage",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectMessage.created_at",
    )
    note_threads = relationship(
        "NoteThread",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="NoteThread.created_at",
    )
    reports = relationship(
        "Report",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="Report.created_at",
    )
    edit_records = relationship(
        "ProjectEdit",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectEdit.created_at",
    )


class ProjectEdit(Base):
    """Durable database index for the filesystem edit journal."""

    __tablename__ = "project_edits"
    __table_args__ = (
        UniqueConstraint("transaction_id", name="uq_project_edits_transaction_id"),
        Index("ix_project_edits_project_created", "project_id", "created_at"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    transaction_id = Column(String(64), nullable=False)
    origin = Column(String(50), nullable=False, default="agent")
    summary = Column(Text, nullable=False, default="")
    status = Column(String(32), nullable=False, default="committed")
    files = Column(JSON, nullable=False, default=list)
    diagnostics = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    committed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    reverted_by = Column(String(64))

    project = relationship("Project", back_populates="edit_records")


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False)
    file_role = Column(String(50))
    original_name = Column(String(500))
    detected_format = Column(String(50))
    file_summary = Column(JSON)
    file_path = Column(String(500))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    project = relationship("Project", back_populates="files")


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False)
    job_type = Column(String(50))
    status = Column(String(50), default="pending")
    progress = Column(JSON)
    logs = Column(Text)
    error = Column(Text)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    project = relationship("Project", back_populates="jobs")


class ProjectMessage(Base):
    __tablename__ = "project_messages"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    role = Column(String(20), nullable=False)
    kind = Column(String(30), default="message", nullable=False)
    content = Column(Text, nullable=False)
    message_metadata = Column("metadata", JSON)
    # Nullable so messages created before NoteThread cells remain valid.
    cell_id = Column(String(36), index=True)
    cell_type = Column(String(32))
    cell_revision = Column(Integer)
    execution_id = Column(String(36), index=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    project = relationship("Project", back_populates="messages")
