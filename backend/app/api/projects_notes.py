"""Tenant-scoped NoteThread and cell revision endpoints.

These endpoints establish the notebook domain without renaming or replacing
the legacy project chat or Quarto report routes.
"""

from __future__ import annotations

from datetime import datetime, timezone
import asyncio
import hashlib
import shutil
import time
from pathlib import Path
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.auth import get_current_tenant, get_current_user_id, get_project_for_tenant
from app.database import get_db
from app.models.notes import (
    CellExecution,
    NoteCell,
    NoteCellRevision,
    NoteExecutionArtifact,
    NoteExecutionEvent,
    NoteThread,
    Report,
)
from app.models.project import Project
from app.schemas.schemas import (
    NoteCellCreate,
    NoteCellOut,
    NoteCellExecutionCreate,
    NoteCellExecutionOut,
    NoteCellRevisionCreate,
    NoteCellRevisionOut,
    NoteThreadCreate,
    NoteThreadAttach,
    NoteThreadWorkspaceCreate,
    NoteThreadOut,
    NoteThreadSummaryOut,
    NoteThreadUpdate,
    NoteThreadTurnRequest,
    NoteThreadReportExportRequest,
    DatasetImportRequest,
    ReportOut,
)
from app.config import settings
from app.services.agent_runtime import normalise_cell_type
from app.services.note_scope import standalone_storage_path
from app.services.note_agent import append_note_cell, conversation_from_cells, stream_note_agent
from app.services.note_data import MAX_THREAD_UPLOAD_BYTES
from app.services.note_report import build_note_qmd, report_payload, safe_report_slug
from app.services.note_execution import (
    environment_fingerprint,
    input_fingerprint,
    normalise_note_language,
    SUPPORTED_NOTE_LANGUAGES,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_thread(db: Session, project_id: str, thread_id: str) -> NoteThread:
    thread = (
        db.query(NoteThread)
        .options(selectinload(NoteThread.cells).selectinload(NoteCell.revisions).selectinload(NoteCellRevision.executions).selectinload(CellExecution.artifacts))
        .filter(NoteThread.id == thread_id, NoteThread.project_id == project_id)
        .first()
    )
    if not thread:
        raise HTTPException(status_code=404, detail="NoteThread not found")
    return thread


def _get_cell(db: Session, thread_id: str, cell_id: str) -> NoteCell:
    cell = (
        db.query(NoteCell)
        .options(selectinload(NoteCell.revisions).selectinload(NoteCellRevision.executions).selectinload(CellExecution.artifacts))
        .filter(NoteCell.id == cell_id, NoteCell.thread_id == thread_id)
        .first()
    )
    if not cell:
        raise HTTPException(status_code=404, detail="NoteCell not found")
    return cell


def _revision_payload(revision: NoteCellRevision) -> dict:
    return {
        "id": str(revision.id),
        "cell_id": str(revision.cell_id),
        "revision": revision.revision,
        "cell_type": revision.cell_type,
        "language": revision.language,
        "content": revision.content,
        "metadata": revision.revision_metadata,
        "created_by": revision.created_by,
        "created_at": revision.created_at,
    }


def _cell_payload(cell: NoteCell) -> dict:
    latest_revision = cell.revisions[-1] if cell.revisions else None
    executions = list(getattr(latest_revision, "executions", []) or []) if latest_revision else []
    latest_execution = max(executions, key=lambda item: item.created_at, default=None)
    return {
        "id": str(cell.id),
        "thread_id": str(cell.thread_id),
        "position": cell.position,
        "status": cell.status,
        "revisions": [_revision_payload(item) for item in cell.revisions],
        "latest_execution": _execution_payload(latest_execution) if latest_execution else None,
        "created_at": cell.created_at,
        "updated_at": cell.updated_at,
    }



def _artifact_payload(artifact: NoteExecutionArtifact) -> dict:
    return {
        "id": str(artifact.id),
        "execution_id": str(artifact.execution_id),
        "artifact_type": artifact.artifact_type,
        "relative_path": artifact.relative_path,
        "mime_type": artifact.mime_type,
        "byte_size": artifact.byte_size,
        "sha256": artifact.sha256,
        "metadata": artifact.artifact_metadata,
        "created_at": artifact.created_at,
    }


def _execution_payload(execution: CellExecution) -> dict:
    return {
        "id": str(execution.id),
        "revision_id": str(execution.revision_id),
        "attempt": execution.attempt,
        "status": execution.status,
        "execution_kind": execution.execution_kind,
        "timeout_seconds": execution.timeout_seconds,
        "cancel_requested": bool(execution.cancel_requested),
        "environment_fingerprint": execution.environment_fingerprint,
        "input_fingerprint": execution.input_fingerprint,
        "parameters": execution.parameters,
        "result_metadata": execution.result_metadata,
        "artifacts": [_artifact_payload(item) for item in execution.artifacts],
        "error": execution.error,
        "started_at": execution.started_at,
        "finished_at": execution.finished_at,
        "created_at": execution.created_at,
        "event_sequence": int(execution.event_sequence or 0),
        "cache_policy": execution.cache_policy,
        "cache_key": execution.cache_key,
        "dependency_fingerprint": execution.dependency_fingerprint,
        "upstream_execution_ids": execution.upstream_execution_ids or [],
        "cache_hit": bool(execution.cache_hit),
        "cache_source_execution_id": execution.cache_source_execution_id,
    }


def _event_payload(event: NoteExecutionEvent) -> dict:
    return {
        "id": str(event.id),
        "execution_id": str(event.execution_id),
        "sequence": event.sequence,
        "event_type": event.event_type,
        "status": event.status,
        "payload": event.event_payload or {},
        "created_at": event.created_at,
    }


def _get_execution(db: Session, project_id: str, thread_id: str, cell_id: str, execution_id: str) -> CellExecution:
    execution = (
        db.query(CellExecution)
        .join(NoteCellRevision, NoteCellRevision.id == CellExecution.revision_id)
        .join(NoteCell, NoteCell.id == NoteCellRevision.cell_id)
        .join(NoteThread, NoteThread.id == NoteCell.thread_id)
        .filter(
            CellExecution.id == execution_id,
            NoteCell.id == cell_id,
            NoteCell.thread_id == thread_id,
            NoteThread.project_id == project_id,
        )
        .first()
    )
    if not execution:
        raise HTTPException(status_code=404, detail="Cell execution not found")
    return execution


def _publish_execution_event(project_id: str, thread_id: str, execution: CellExecution, event_type: str) -> None:
    from app.services.job_events import publish_project_event

    publish_project_event(
        project_id,
        {
            "note_thread_id": thread_id,
            "note_cell_id": str(execution.revision_record.cell_id),
            "note_execution_id": str(execution.id),
            "event_type": event_type,
            "execution_status": execution.status,
            "cancel_requested": bool(execution.cancel_requested),
            "execution_event_sequence": int(execution.event_sequence or 0),
        },
    )


def _thread_summary_payload(thread: NoteThread) -> dict:
    return {
        "id": str(thread.id),
        "project_id": str(thread.project_id) if thread.project_id else None,
        "scope": "workspace" if thread.project_id else "standalone",
        "title": thread.title,
        "thread_type": thread.thread_type,
        "status": thread.status,
        "metadata": thread.thread_metadata,
        "created_at": thread.created_at,
        "updated_at": thread.updated_at,
    }


def _thread_payload(thread: NoteThread) -> dict:
    payload = _thread_summary_payload(thread)
    payload["cells"] = [_cell_payload(cell) for cell in thread.cells]
    return payload


def _publish_note_event(project_id: str, thread_id: str, event_type: str) -> None:
    from app.services.job_events import publish_project_event

    publish_project_event(
        project_id,
        {"note_thread_id": thread_id, "event_type": event_type},
    )


@router.get("/{project_id}/notes", response_model=list[NoteThreadSummaryOut])
def list_note_threads(
    project_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """List note threads without loading their potentially large cell contents."""
    get_project_for_tenant(db, project_id, tenant_id)
    threads = (
        db.query(NoteThread)
        .filter(NoteThread.project_id == project_id)
        .order_by(NoteThread.updated_at.desc())
        .all()
    )
    return [_thread_summary_payload(thread) for thread in threads]


@router.post("/{project_id}/notes", response_model=NoteThreadOut, status_code=status.HTTP_201_CREATED)
def create_note_thread(
    project_id: str,
    data: NoteThreadCreate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
    user_id: str = Depends(get_current_user_id),
):
    """Create a notebook thread inside the tenant-owned workspace."""
    get_project_for_tenant(db, project_id, tenant_id)
    thread = NoteThread(
        project_id=project_id,
        tenant_id=tenant_id,
        owner_id=user_id,
        title=data.title.strip(),
        thread_type=data.thread_type.strip().lower(),
        status="active",
    )
    db.add(thread)
    db.commit()
    db.refresh(thread)
    _publish_note_event(project_id, str(thread.id), "note_thread_created")
    return _thread_payload(thread)


@router.post("/{project_id}/notes/{thread_id}/files", status_code=status.HTTP_201_CREATED)
def upload_note_thread_file(
    project_id: str,
    thread_id: str,
    file: UploadFile,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Attach one data file to a project NoteThread so the agent can inspect it."""
    from app.services.note_data import save_thread_upload

    project = get_project_for_tenant(db, project_id, tenant_id)
    thread = _get_note_thread_for_tenant(db, thread_id, tenant_id)
    if thread.status != "active":
        raise HTTPException(status_code=409, detail="Cannot attach files to an archived NoteThread")
    content = file.file.read(MAX_THREAD_UPLOAD_BYTES + 1)
    try:
        summary = save_thread_upload(thread, content, filename=file.filename or "upload.bin", project=project)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    thread.updated_at = _now()
    db.commit()
    return summary


@router.get("/{project_id}/notes/{thread_id}/files")
def list_note_thread_files(
    project_id: str,
    thread_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    from app.services.note_data import list_thread_data_files

    project = get_project_for_tenant(db, project_id, tenant_id)
    thread = _get_note_thread_for_tenant(db, thread_id, tenant_id)
    return list_thread_data_files(thread, project=project)


@router.get("/{project_id}/notes/{thread_id}", response_model=NoteThreadOut)
def get_note_thread(
    project_id: str,
    thread_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Load one note thread and all immutable cell revisions."""
    get_project_for_tenant(db, project_id, tenant_id)
    return _thread_payload(_get_thread(db, project_id, thread_id))


@router.patch("/{project_id}/notes/{thread_id}", response_model=NoteThreadSummaryOut)
def update_note_thread(
    project_id: str,
    thread_id: str,
    data: NoteThreadUpdate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Update thread presentation state; cell revisions remain append-only."""
    get_project_for_tenant(db, project_id, tenant_id)
    thread = _get_thread(db, project_id, thread_id)
    changes = data.model_dump(exclude_unset=True)
    if "title" in changes:
        thread.title = changes["title"].strip()
    if "status" in changes:
        thread.status = changes["status"]
    if "metadata" in changes:
        thread.thread_metadata = changes["metadata"]
    db.commit()
    db.refresh(thread)
    _publish_note_event(project_id, thread_id, "note_thread_updated")
    return _thread_summary_payload(thread)


def _delete_note_thread_files(db: Session, thread: NoteThread, project: Project | None = None) -> None:
    """Remove on-disk artifacts owned by one NoteThread (kept separate from
    the shared workspace, which belongs to the whole project/thread root)."""
    from app.services.note_scope import thread_storage_path
    from app.services.note_execution import NOTE_OUTPUT_ROOT

    try:
        base = thread_storage_path(thread, project)
    except (OSError, ValueError):
        return
    executions = (
        db.query(CellExecution)
        .join(NoteCellRevision, NoteCellRevision.id == CellExecution.revision_id)
        .join(NoteCell, NoteCell.id == NoteCellRevision.cell_id)
        .filter(NoteCell.thread_id == thread.id)
        .all()
    )
    for execution in executions:
        run_dir = base / ".omicsbase" / "note-executions" / str(execution.id)
        shutil.rmtree(run_dir, ignore_errors=True)
        console_dir = base / NOTE_OUTPUT_ROOT / str(execution.id)
        shutil.rmtree(console_dir, ignore_errors=True)


@router.delete("/{project_id}/notes/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note_thread(
    project_id: str,
    thread_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Permanently delete a workspace NoteThread, its executions, and files."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    thread = _get_thread(db, project_id, thread_id)
    _delete_note_thread_files(db, thread, project)
    db.delete(thread)
    db.commit()
    _publish_note_event(project_id, thread_id, "note_thread_deleted")


@router.post("/{project_id}/notes/{thread_id}/cells", response_model=NoteCellOut, status_code=status.HTTP_201_CREATED)
def create_note_cell(
    project_id: str,
    thread_id: str,
    data: NoteCellCreate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
    user_id: str = Depends(get_current_user_id),
):
    """Append a new cell with immutable revision one."""
    get_project_for_tenant(db, project_id, tenant_id)
    thread = _get_thread(db, project_id, thread_id)
    if thread.status != "active":
        raise HTTPException(status_code=409, detail="Cannot add cells to an archived NoteThread")

    position = data.position
    if position is None:
        current_max = (
            db.query(func.max(NoteCell.position))
            .filter(NoteCell.thread_id == thread_id)
            .scalar()
        )
        position = (current_max if current_max is not None else -1) + 1

    cell = NoteCell(thread_id=thread_id, position=position, status="active")
    revision = NoteCellRevision(
        revision=1,
        cell_type=normalise_cell_type(data.cell_type),
        language=data.language,
        content=data.content,
        revision_metadata=data.metadata,
        created_by=user_id,
    )
    cell.revisions.append(revision)
    thread.updated_at = _now()
    db.add(cell)
    db.commit()
    db.refresh(cell)
    _publish_note_event(project_id, thread_id, "note_cell_created")
    return _cell_payload(cell)


@router.get("/{project_id}/notes/{thread_id}/cells/{cell_id}", response_model=NoteCellOut)
def get_note_cell(
    project_id: str,
    thread_id: str,
    cell_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Load one cell identity and all of its immutable revisions."""
    get_project_for_tenant(db, project_id, tenant_id)
    _get_thread(db, project_id, thread_id)
    return _cell_payload(_get_cell(db, thread_id, cell_id))


@router.post("/{project_id}/notes/{thread_id}/cells/{cell_id}/revisions", response_model=NoteCellRevisionOut, status_code=status.HTTP_201_CREATED)
def append_note_cell_revision(
    project_id: str,
    thread_id: str,
    cell_id: str,
    data: NoteCellRevisionCreate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
    user_id: str = Depends(get_current_user_id),
):
    """Append a new revision; existing source rows are never updated."""
    get_project_for_tenant(db, project_id, tenant_id)
    thread = _get_thread(db, project_id, thread_id)
    if thread.status != "active":
        raise HTTPException(status_code=409, detail="Cannot revise an archived NoteThread")
    cell = _get_cell(db, thread_id, cell_id)
    if cell.status != "active":
        raise HTTPException(status_code=409, detail="Cannot revise an inactive NoteCell")

    for _ in range(2):
        latest = (
            db.query(NoteCellRevision)
            .filter(NoteCellRevision.cell_id == cell_id)
            .order_by(NoteCellRevision.revision.desc())
            .first()
        )
        next_revision = (latest.revision if latest else 0) + 1
        revision = NoteCellRevision(
            cell_id=cell_id,
            revision=next_revision,
            cell_type=normalise_cell_type(data.cell_type),
            language=data.language,
            content=data.content,
            revision_metadata=data.metadata,
            created_by=user_id,
        )
        cell.updated_at = _now()
        thread.updated_at = cell.updated_at
        db.add(revision)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        db.refresh(revision)
        _publish_note_event(project_id, thread_id, "note_cell_revision_created")
        return _revision_payload(revision)

    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Concurrent cell revision detected; retry the request",
    )


@router.get("/{project_id}/reports", response_model=list[ReportOut])
def list_reports(
    project_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """List published/reporting surfaces separately from NoteThreads."""
    get_project_for_tenant(db, project_id, tenant_id)
    reports = (
        db.query(Report)
        .filter(Report.project_id == project_id)
        .order_by(Report.updated_at.desc())
        .all()
    )
    return [
        {
            "id": str(report.id),
            "project_id": str(report.project_id),
            "name": report.name,
            "slug": report.slug,
            "report_type": report.report_type,
            "status": report.status,
            "source_path": report.source_path,
            "rendered_path": report.rendered_path,
            "metadata": report.report_metadata,
            "created_at": report.created_at,
            "updated_at": report.updated_at,
        }
        for report in reports
    ]




@router.post(
    "/{project_id}/reports/from-note/{thread_id}",
    response_model=ReportOut,
    status_code=status.HTTP_201_CREATED,
)
def export_note_thread_report(
    project_id: str,
    thread_id: str,
    data: NoteThreadReportExportRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Export an attached NoteThread as draft Quarto source without rendering it."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    if not project.project_dir or not Path(project.project_dir).is_dir():
        raise HTTPException(
            status_code=409,
            detail="Generate the Workspace before exporting a NoteThread report",
        )
    thread = _get_thread(db, project_id, thread_id)
    slug = safe_report_slug(data.slug, thread_id)
    name = (data.name or thread.title or "NoteThread report").strip()
    base = Path(project.project_dir).resolve()
    relative_source = Path("code") / "notes" / f"{slug}.qmd"
    source_path = (base / relative_source).resolve()
    source_path.relative_to(base)
    content, metadata = build_note_qmd(db, thread)
    existing = (
        db.query(Report)
        .filter(Report.project_id == project_id, Report.slug == slug)
        .first()
    )
    if existing is not None and not data.overwrite:
        existing_metadata = existing.report_metadata or {}
        if (
            existing_metadata.get("source_note_thread_id") == str(thread.id)
            and source_path.is_file()
            and existing_metadata.get("source_sha256") == metadata["source_sha256"]
            and hashlib.sha256(source_path.read_bytes()).hexdigest() == metadata["source_sha256"]
        ):
            return report_payload(existing)
        raise HTTPException(
            status_code=409,
            detail="A report with this slug already exists; pass overwrite=true to replace its source",
        )
    if source_path.exists() and not data.overwrite:
        raise HTTPException(
            status_code=409,
            detail="The Quarto source path already exists; choose another slug or pass overwrite=true",
        )

    source_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = source_path.with_name(source_path.name + ".tmp")
    temporary_path.write_text(content, encoding="utf-8")
    temporary_path.replace(source_path)
    if existing is None:
        existing = Report(
            project_id=project_id,
            name=name,
            slug=slug,
            report_type="quarto",
            status="draft",
            source_path=relative_source.as_posix(),
            rendered_path=None,
            report_metadata=metadata,
        )
        db.add(existing)
    else:
        existing.name = name
        existing.status = "draft"
        existing.source_path = relative_source.as_posix()
        existing.rendered_path = None
        existing.report_metadata = metadata
        existing.updated_at = _now()
    db.commit()
    db.refresh(existing)
    from app.services.agent_runtime import record_agent_action

    record_agent_action(
        db,
        project,
        "report",
        "completed",
        "Exported NoteThread as draft Quarto source",
        {"note_thread_id": str(thread.id), "report_id": str(existing.id)},
        files=[relative_source.as_posix()],
    )
    return report_payload(existing)


def _get_note_thread_for_tenant(db: Session, thread_id: str, tenant_id: str) -> NoteThread:
    thread = (
        db.query(NoteThread)
        .options(selectinload(NoteThread.cells).selectinload(NoteCell.revisions).selectinload(NoteCellRevision.executions).selectinload(CellExecution.artifacts))
        .filter(NoteThread.id == thread_id, NoteThread.tenant_id == tenant_id)
        .first()
    )
    if not thread:
        raise HTTPException(status_code=404, detail="NoteThread not found")
    return thread


def _promote_cell_to_workspace(
    db: Session,
    thread: NoteThread,
    arguments: dict[str, Any],
    *,
    turn_id: str | None = None,
) -> dict[str, Any]:
    """Copy a tested cell into the project's code directory (guarded write)."""
    from pathlib import Path

    from app.services.apply_edits import is_path_locked, safe_resolve_path

    if not thread.project_id:
        return {
            "status": "error",
            "error": "This notebook is not attached to a project. Attach it first, then promote.",
            "turn_id": turn_id,
        }
    project = db.query(Project).filter(Project.id == thread.project_id).first()
    base = Path(project.project_dir).resolve() if project and project.project_dir else None
    if not base or not base.exists():
        return {
            "status": "error",
            "error": "The project has no generated workspace yet. Build the report first, then promote.",
            "turn_id": turn_id,
        }

    relative_path = str(arguments.get("path") or "").strip()
    content = str(arguments.get("content") or "")
    if not relative_path or not content.strip():
        return {"status": "error", "error": "promote_to_workspace needs a path and cell content.", "turn_id": turn_id}
    if not relative_path.lower().endswith((".r", ".qmd", ".md")):
        return {"status": "error", "error": "Only .R, .qmd, and .md files can be promoted.", "turn_id": turn_id}
    if len(content) > 200_000:
        return {"status": "error", "error": "The promoted content exceeds the 200 KB limit.", "turn_id": turn_id}

    project_relative = f"code/{relative_path}"
    if is_path_locked(base, project_relative):
        return {
            "status": "error",
            "error": f"{project_relative} is locked. Unlock it in the workspace before promoting.",
            "turn_id": turn_id,
        }
    code_dir = base / "code"
    target = safe_resolve_path(code_dir, relative_path)
    if target is None:
        return {"status": "error", "error": "The path escapes the project code directory.", "turn_id": turn_id}

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {
        "status": "ok",
        "path": project_relative,
        "promoted": True,
        "turn_id": turn_id,
    }


def _note_agent_context(db: Session, thread: NoteThread) -> dict[str, Any]:
    cells: list[dict[str, Any]] = []
    ordered_cells = sorted(thread.cells, key=lambda item: (int(item.position or 0), item.created_at))
    for cell in ordered_cells[-24:]:
        revision = cell.revisions[-1] if cell.revisions else None
        if revision is None:
            continue
        executions = (
            db.query(CellExecution)
            .filter(CellExecution.revision_id == revision.id)
            .order_by(CellExecution.created_at.desc())
            .limit(2)
            .all()
        )
        execution_preview = []
        for execution in executions:
            metadata = execution.result_metadata or {}
            execution_preview.append(
                {
                    "id": str(execution.id),
                    "status": execution.status,
                    "stdout_preview": str(metadata.get("stdout_preview") or "")[:1800],
                    "stderr_preview": str(metadata.get("stderr_preview") or "")[:1200],
                    "error": str(execution.error or "")[:1200],
                }
            )
        cells.append(
            {
                "id": str(cell.id),
                "position": cell.position,
                "cell_type": revision.cell_type,
                "language": revision.language,
                "content": str(revision.content or "")[:6000],
                "executions": execution_preview,
            }
        )

    context: dict[str, Any] = {
        "scope": "workspace" if thread.project_id else "standalone",
        "thread": {
            "id": str(thread.id),
            "title": thread.title,
            "thread_type": thread.thread_type,
            "status": thread.status,
        },
        "cells": cells,
        "workspace_objects": _read_workspace_objects(thread),
    }
    project = None
    if thread.project_id:
        project = db.query(Project).filter(Project.id == thread.project_id).first()
    try:
        from app.services.note_data import list_thread_data_files

        context["data_files"] = list_thread_data_files(thread, project=project)
    except (OSError, ValueError):
        context["data_files"] = []
    if thread.project_id:
        if project is not None:
            context["workspace"] = {
                "id": str(project.id),
                "name": project.name,
                "question": project.question,
                "status": project.status,
                "agent_state": project.agent_state,
                "project_dir_available": bool(project.project_dir),
                "uploaded_files": [
                    {
                        "name": item.original_name,
                        "format": item.detected_format,
                        "columns": ((item.file_summary or {}).get("columns") or [])[:30],
                    }
                    for item in (project.files or [])[:40]
                ],
            }
    return context


async def _wait_for_note_execution(
    db: Session,
    execution_id: str,
    timeout_seconds: int,
    cancel_check: Any = None,
) -> dict[str, Any]:
    """Wait for one queued cell execution to finish and return its payload.

    This gives the note agent a result-aware loop: the model queues a
    cell, sees its real output or error, and can check, explain, or fix it
    before answering. Returns the latest known state when the execution does
    not finish within its timeout budget.
    """
    terminal = {"completed", "completed_with_errors", "failed", "timed_out", "cancelled"}
    if timeout_seconds is None:
        timeout_seconds = int(settings.note_execution_default_timeout_seconds)
    deadline = time.monotonic() + max(1, int(timeout_seconds)) + 60
    while time.monotonic() < deadline:
        if cancel_check and cancel_check():
            break
        execution = db.query(CellExecution).filter(CellExecution.id == execution_id).first()
        if execution is None:
            break
        status = str(execution.status or "")
        if status in terminal:
            return _execution_payload(execution)
        db.expire_all()
        await asyncio.sleep(1)
    execution = db.query(CellExecution).filter(CellExecution.id == execution_id).first()
    if execution is None:
        return {"id": execution_id, "status": "queued", "error": "Execution record disappeared"}
    return _execution_payload(execution)


def _read_workspace_objects(thread: NoteThread) -> list[str]:
    """Return variable names currently present in the thread's shared R workspace."""
    from app.services.note_scope import thread_storage_path

    try:
        objects_path = (
            thread_storage_path(thread) / ".omicsbase" / "note-kernel" / "workspace-objects.txt"
        )
    except (OSError, ValueError):
        return []
    if not objects_path.is_file():
        return []
    try:
        content = objects_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    names = [line.strip() for line in content.splitlines() if line.strip()]
    names = [name for name in names if not name.startswith(".note_")]
    return names[-200:]


note_agent_router = APIRouter(prefix="/api/notes", tags=["notes"])


@note_agent_router.post("/{thread_id}/turn")
async def note_thread_turn(
    thread_id: str,
    data: NoteThreadTurnRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
    user_id: str = Depends(get_current_user_id),
):
    """Run one autonomous turn while persisting every visible action as a cell."""
    from fastapi.responses import StreamingResponse

    message = data.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="The notebook message cannot be blank")

    thread = _get_note_thread_for_tenant(db, thread_id, tenant_id)
    if thread.status != "active":
        raise HTTPException(status_code=409, detail="Cannot add turns to an archived NoteThread")
    from app.services.agent_runs import (
        IdempotencyConflict,
        get_agent_run,
        is_run_task_active,
        session_factory_for,
        register_run_task,
        unregister_run_task,
        approximate_tokens,
        create_or_get_agent_run,
        record_run_telemetry,
        telemetry_from_usage,
        record_stream_event,
        replay_agent_run_stream,
        run_cancel_requested,
        serialize_agent_run,
        transition_agent_run,
    )

    request_payload = data.model_dump(mode="json") if hasattr(data, "model_dump") else data.dict()
    try:
        run, run_created = create_or_get_agent_run(
            db,
            tenant_id=tenant_id,
            owner_id=user_id,
            surface="notes",
            idempotency_scope=f"notes:{thread_id}:turn",
            idempotency_key=data.idempotency_key,
            request_payload=request_payload,
            project_id=str(thread.project_id) if thread.project_id else None,
            note_thread_id=str(thread.id),
        )
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    resume_existing_run = (
        not run_created
        and run.status == "paused"
        and bool(run.resumable)
        and not is_run_task_active(str(run.id))
    )
    if not run_created and not resume_existing_run:
        async def replay_existing_run():
            async for replay_event in replay_agent_run_stream(
                str(run.id),
                tenant_id,
                session_factory=session_factory_for(db),
            ):
                yield json.dumps(replay_event, default=str) + "\n"

        return StreamingResponse(
            replay_existing_run(),
            media_type="application/x-ndjson",
            headers={"X-Agent-Run-ID": str(run.id), "X-Agent-Run-Replayed": "true"},
        )

    turn_id = str(run.id)
    if resume_existing_run:
        transition_agent_run(db, run, "running", event_type="run_resumed")
        run.result_payload = None
        db.commit()
        existing_cells = list(thread.cells)
        user_cell = next(
            (
                cell
                for cell in existing_cells
                if any(
                    str((revision.revision_metadata or {}).get("turn_id")) == turn_id
                    and revision.cell_type == "agent"
                    for revision in cell.revisions
                )
            ),
            None,
        )
        if user_cell is None:
            raise HTTPException(status_code=409, detail="Paused run has no persisted user cell")
        prior_cells = [cell for cell in existing_cells if str(cell.id) != str(user_cell.id)]
    else:
        prior_cells = list(thread.cells)
        user_cell = append_note_cell(
            db,
            thread,
            cell_type="agent",
            content=message,
            metadata={"turn_id": turn_id, "role": "user"},
            created_by=user_id,
        )
        transition_agent_run(db, run, "running", event_type="run_started")
        db.commit()
    active_thread = _get_note_thread_for_tenant(db, thread_id, tenant_id)
    if active_thread.title.strip().lower() == "untitled note":
        generated_title = " ".join(message.split())
        if len(generated_title) > 72:
            generated_title = generated_title[:69].rstrip() + "..."
        active_thread.title = generated_title or active_thread.title
        active_thread.updated_at = _now()
        db.commit()
        active_thread = _get_note_thread_for_tenant(db, thread_id, tenant_id)

    context = _note_agent_context(db, active_thread)
    generated_code_cells = 0
    generated_note_cells = 0
    knowledge_sources: list[str] = []

    async def action_handler(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        nonlocal generated_code_cells, generated_note_cells, knowledge_sources
        fresh_thread = _get_note_thread_for_tenant(db, thread_id, tenant_id)
        if tool_name == "inspect_note":
            return {"status": "ok", "context": _note_agent_context(db, fresh_thread), "turn_id": turn_id}
        if tool_name == "inspect_data_files":
            from app.services.note_data import list_thread_data_files

            project = None
            if fresh_thread.project_id:
                project = db.query(Project).filter(Project.id == fresh_thread.project_id).first()
            return {
                "status": "ok",
                "files": list_thread_data_files(fresh_thread, project=project),
                "turn_id": turn_id,
            }
        if tool_name == "promote_to_workspace":
            return _promote_cell_to_workspace(db, fresh_thread, arguments, turn_id=turn_id)
        if tool_name == "search_bioc_books":
            from app.services.bioc_knowledge import search_bioc_knowledge

            query = str(arguments.get("query") or message).strip()
            channel = str(arguments.get("channel") or "stable").strip().lower()
            try:
                limit = int(arguments.get("limit") or 5)
            except (TypeError, ValueError):
                limit = 5
            result = search_bioc_knowledge(
                db,
                query,
                channel=channel,
                limit=max(1, min(limit, 8)),
                source_slug=str(arguments.get("book") or "").strip() or None,
            )
            for match in result.get("matches") or []:
                citation = str(match.get("citation") or "").strip()
                if citation and citation not in knowledge_sources:
                    knowledge_sources.append(citation)
            return {**result, "turn_id": turn_id}
        if tool_name == "add_note":
            text = str(arguments.get("text") or "").strip()
            if not text:
                return {"status": "error", "error": "The note text was empty", "turn_id": turn_id}
            if len(text) > 8_000:
                return {"status": "error", "error": "The note exceeds the 8000 character limit", "turn_id": turn_id}
            if generated_note_cells >= 6:
                return {"status": "error", "error": "This turn reached the generated note limit", "turn_id": turn_id}
            generated_note_cells += 1
            note_cell = append_note_cell(
                db,
                fresh_thread,
                cell_type="markdown",
                content=text,
                metadata={
                    "turn_id": turn_id,
                    "role": "assistant",
                    "generated_by": "note_agent",
                    "knowledge_sources": knowledge_sources[-12:],
                },
                created_by="agent",
            )
            return {"status": "ok", "cell": _cell_payload(note_cell), "turn_id": turn_id}
        if tool_name != "run_r_cell":
            return {"status": "error", "error": "Unsupported NoteThread action", "turn_id": turn_id}
        code = str(arguments.get("code") or "").strip()
        if not code:
            return {"status": "error", "error": "The R cell was empty", "turn_id": turn_id}
        if len(code) > 2_000_000:
            return {"status": "error", "error": "The R cell exceeds the 2 MB limit", "turn_id": turn_id}
        if generated_code_cells >= 6:
            return {"status": "error", "error": "This turn reached the generated R-cell limit", "turn_id": turn_id}
        parameters = arguments.get("parameters")
        if not isinstance(parameters, dict):
            parameters = {}
        timeout_seconds = arguments.get("timeout_seconds")
        if timeout_seconds is None:
            timeout_seconds = int(settings.note_execution_default_timeout_seconds)
        else:
            try:
                timeout_seconds = int(timeout_seconds)
            except (TypeError, ValueError):
                return {"status": "error", "error": "timeout_seconds must be an integer", "turn_id": turn_id}
        purpose = str(arguments.get("purpose") or "Notebook computation")[:1000]
        generated_code_cells += 1
        cell = append_note_cell(
            db,
            fresh_thread,
            cell_type="code",
            language="r",
            content=code,
            metadata={
                "turn_id": turn_id,
                "role": "assistant",
                "generated_by": "note_agent",
                "purpose": purpose,
                "knowledge_sources": knowledge_sources[-12:],
            },
            created_by="agent",
        )
        cell_payload = _cell_payload(cell)
        if not data.auto_execute:
            return {"status": "ok", "cell": cell_payload, "turn_id": turn_id}

        from app.api.projects_note_executions import execute_note_cell, execute_standalone_note_cell

        execution_request = NoteCellExecutionCreate(
            revision=1,
            parameters=parameters,
            timeout_seconds=timeout_seconds,
            cache_policy="off",
        )
        if fresh_thread.project_id:
            execution_payload = execute_note_cell(
                str(fresh_thread.project_id),
                thread_id,
                str(cell.id),
                execution_request,
                background_tasks,
                db,
                tenant_id,
            )
        else:
            execution_payload = execute_standalone_note_cell(
                thread_id,
                str(cell.id),
                execution_request,
                background_tasks,
                db,
                tenant_id,
            )
        if (
            settings.note_execution_agent_wait_enabled
            and str(execution_payload.get("status") or "") in {"queued", "running", "cancel_requested"}
        ):
            execution_payload = await _wait_for_note_execution(
                db,
                str(execution_payload["id"]),
                timeout_seconds,
                cancel_check=lambda: run_cancel_requested(db, str(run.id)),
            )
        return {
            "status": "ok",
            "stdout": str(((execution_payload or {}).get("result_metadata") or {}).get("stdout_preview") or "")[:4000],
            "stderr": (
                str(execution_payload.get("error") or "")[:4000]
                if str(execution_payload.get("status") or "") in {"failed", "timed_out", "cancelled"}
                else ""
            ),
            "summary": {
                "execution_status": str(execution_payload.get("status") or "queued"),
                "output_chars": ((execution_payload or {}).get("result_metadata") or {}).get("output_chars", 0),
                "output_truncated": bool(((execution_payload or {}).get("result_metadata") or {}).get("output_truncated")),
                "had_errors": bool(((execution_payload or {}).get("result_metadata") or {}).get("had_errors")),
            },
            "cell": cell_payload,
            "execution": execution_payload,
            "turn_id": turn_id,
        }

    request_db = db
    worker_session_factory = session_factory_for(request_db)
    worker_db = worker_session_factory()
    worker_thread = _get_note_thread_for_tenant(worker_db, thread_id, tenant_id)
    worker_run = get_agent_run(worker_db, str(run.id), tenant_id)
    if worker_run is None:
        worker_db.close()
        raise HTTPException(status_code=404, detail="Agent run disappeared before execution")
    worker_user_cell = (
        worker_db.query(NoteCell)
        .filter(NoteCell.id == str(user_cell.id))
        .first()
    )
    if worker_user_cell is None:
        worker_db.close()
        raise HTTPException(status_code=404, detail="Note cell disappeared before execution")
    worker_prior_cells = [
        cell for cell in worker_thread.cells if str(cell.id) != str(user_cell.id)
    ]
    request_db.close()
    db = worker_db
    thread = worker_thread
    active_thread = worker_thread
    run = worker_run
    user_cell = worker_user_cell
    prior_cells = worker_prior_cells
    context = _note_agent_context(worker_db, worker_thread)

    async def event_stream():
        final_written = False
        turn_started = asyncio.get_running_loop().time()
        output_chars = 0
        provider_usage: dict[str, int] = {}
        tool_started_at = {}
        token_buffer: list[str] = []
        telemetry_written = False
        record_stream_event(db, run, {"type": "note_cell", "role": "user", "turn_id": turn_id, "cell": _cell_payload(user_cell)})
        db.commit()
        yield json.dumps({"type": "run", "run": serialize_agent_run(run)}, default=str) + "\n"
        yield json.dumps(
            {"type": "note_cell", "role": "user", "turn_id": turn_id, "cell": _cell_payload(user_cell)},
        default=str) + "\n"
        yield json.dumps(
            {"type": "thread_updated", "turn_id": turn_id, "thread": _thread_summary_payload(active_thread)},
        default=str) + "\n"
        try:
            async for event in stream_note_agent(
                message=message,
                cells=prior_cells,
                context=context,
                action_handler=action_handler,
                cancel_check=lambda: run_cancel_requested(db, str(run.id)),
            ):
                output_event = dict(event)
                output_event["turn_id"] = turn_id
                event_type = str(output_event.get("type") or "stream_event")
                if event_type == "usage":
                    usage = output_event.get("usage")
                    if isinstance(usage, dict):
                        for key, value in usage.items():
                            try:
                                provider_usage[str(key)] = int(value)
                            except (TypeError, ValueError):
                                continue
                    continue
                if event_type == "token":
                    token = str(output_event.get("token") or "")
                    output_chars += len(token)
                    if token:
                        token_buffer.append(token)
                        if sum(len(item) for item in token_buffer) >= 512:
                            record_stream_event(
                                db,
                                run,
                                {"type": "token_chunk", "token": "".join(token_buffer)},
                            )
                            token_buffer.clear()
                if event_type == "tool_started":
                    tool_started_at[str(output_event.get("tool_call_id") or output_event.get("tool") or "unknown")] = asyncio.get_running_loop().time()
                    if run.status == "running":
                        transition_agent_run(db, run, "waiting_tool", event_type="tool_waiting", payload={"tool": output_event.get("tool")})
                elif event_type in {"tool_completed", "execution_queued"} and run.status == "waiting_tool":
                    transition_agent_run(db, run, "running", event_type="tool_resumed")
                if event_type == "final":
                    final_text = str(output_event.get("message") or "").strip()
                    if final_text:
                        final_thread = _get_note_thread_for_tenant(db, thread_id, tenant_id)
                        assistant_cell = append_note_cell(
                            db,
                            final_thread,
                            cell_type="markdown",
                            content=final_text,
                            metadata={
                                "turn_id": turn_id,
                                "role": "assistant",
                                "generated_by": "note_agent",
                                "knowledge_sources": knowledge_sources[-12:],
                            },
                            created_by="agent",
                        )
                        output_event["role"] = "assistant"
                        output_event["cell"] = _cell_payload(assistant_cell)
                        final_written = True
                event_type = str(output_event.get("type") or event_type)
                if event_type in {"final", "cancelled"} and token_buffer:
                    record_stream_event(
                        db,
                        run,
                        {"type": "token_chunk", "token": "".join(token_buffer)},
                    )
                    token_buffer.clear()
                if event_type != "token":
                    replay_event = record_stream_event(db, run, output_event)
                    output_event["run_id"] = str(run.id)
                    output_event["run_sequence"] = replay_event.sequence
                if event_type == "tool_completed":
                    tool_key = str(output_event.get("tool_call_id") or output_event.get("tool") or "unknown")
                    started = tool_started_at.pop(tool_key, None)
                    record_run_telemetry(
                        db,
                        run,
                        kind="tool",
                        operation=str(output_event.get("tool") or "note_tool"),
                        status=str(output_event.get("status") or "completed"),
                        duration_ms=((asyncio.get_running_loop().time() - started) * 1000) if started else None,
                        provider=settings.llm_provider,
                        model=settings.llm_model,
                        error=output_event.get("summary") if output_event.get("status") == "error" else None,
                        metadata={"step": output_event.get("step"), "tool_call_id": output_event.get("tool_call_id")},
                    )
                if event_type in {"final", "cancelled"}:
                    cancelled = event_type == "cancelled" or run_cancel_requested(db, str(run.id))
                    target_status = "cancelled" if cancelled else "completed"
                    if run.status not in {"completed", "failed", "cancelled"}:
                        transition_agent_run(
                            db,
                            run,
                            target_status,
                            event_type="run_cancelled" if cancelled else "run_completed",
                            payload={"cell_id": str((output_event.get("cell") or {}).get("id")) if isinstance(output_event.get("cell"), dict) else None},
                        )
                    run.result_payload = {"cell_id": (output_event.get("cell") or {}).get("id") if isinstance(output_event.get("cell"), dict) else None, "event_type": event_type}
                    if not telemetry_written:
                        record_run_telemetry(
                            db,
                            run,
                            kind="agent",
                            operation="note_turn",
                            status="cancelled" if cancelled else "completed",
                            duration_ms=(asyncio.get_running_loop().time() - turn_started) * 1000,
                            provider=settings.llm_provider,
                            model=settings.llm_model,
                            **telemetry_from_usage(
                                provider_usage,
                                fallback_input_tokens=approximate_tokens(message),
                                fallback_output_tokens=max(0, (output_chars + 3) // 4),
                                metadata={"auto_execute": data.auto_execute},
                            ),
                        )
                        telemetry_written = True
                db.commit()
                yield json.dumps(output_event, default=str) + "\n"
        except asyncio.CancelledError:
            try:
                if run.status not in {"completed", "failed", "cancelled"}:
                    cancelled = run_cancel_requested(db, str(run.id))
                    transition_agent_run(
                        db,
                        run,
                        "cancelled" if cancelled else "paused",
                        event_type="run_cancelled" if cancelled else "run_paused",
                        payload={"reason": "stream disconnected"},
                    )
                    if not telemetry_written:
                        record_run_telemetry(
                            db,
                            run,
                            kind="agent",
                            operation="note_turn",
                            status="cancelled" if cancelled else "paused",
                            duration_ms=(asyncio.get_running_loop().time() - turn_started) * 1000,
                            input_tokens=approximate_tokens(message),
                            output_tokens=max(0, (output_chars + 3) // 4),
                            provider=settings.llm_provider,
                            model=settings.llm_model,
                            metadata={"estimated_tokens": True, "disconnect": True},
                        )
                    db.commit()
            finally:
                raise
        except Exception as exc:
            logger.exception("NoteThread turn failed: %s", exc)
            try:
                if run.status not in {"completed", "failed", "cancelled"}:
                    transition_agent_run(db, run, "failed", event_type="run_failed", payload={"error": str(exc)[:1000]})
                    if not telemetry_written:
                        record_run_telemetry(
                            db,
                            run,
                            kind="agent",
                            operation="note_turn",
                            status="failed",
                            duration_ms=(asyncio.get_running_loop().time() - turn_started) * 1000,
                            input_tokens=approximate_tokens(message),
                            output_tokens=max(0, (output_chars + 3) // 4),
                            provider=settings.llm_provider,
                            model=settings.llm_model,
                            error=str(exc),
                            metadata={"estimated_tokens": True},
                        )
                    db.commit()
            except Exception:
                db.rollback()
            fallback = (
                "I preserved your question in this notebook, but I could not complete the agent turn. "
                "No unrequested computation was run. You can retry or continue from the saved cells."
            )
            if not final_written:
                try:
                    final_thread = _get_note_thread_for_tenant(db, thread_id, tenant_id)
                    assistant_cell = append_note_cell(
                        db,
                        final_thread,
                        cell_type="markdown",
                        content=fallback,
                        metadata={"turn_id": turn_id, "role": "assistant", "generated_by": "note_agent"},
                        created_by="agent",
                    )
                    final_written = True
                    yield json.dumps(
                        {
                            "type": "error",
                            "turn_id": turn_id,
                            "message": fallback,
                        },
                    default=str) + "\n"
                    yield json.dumps(
                        {
                            "type": "final",
                            "turn_id": turn_id,
                            "role": "assistant",
                            "message": fallback,
                            "cell": _cell_payload(assistant_cell),
                        },
                    default=str) + "\n"
                except Exception:
                    logger.exception("Could not persist NoteThread fallback cell")

    async def consume_run():
        worker_task = asyncio.current_task()
        try:
            async for _ in event_stream():
                pass
        except asyncio.CancelledError:
            pause_db = worker_session_factory()
            try:
                paused_run = get_agent_run(pause_db, str(run.id), tenant_id)
                if paused_run and paused_run.status not in {"completed", "failed", "cancelled"}:
                    target = "cancelled" if paused_run.cancel_requested else "paused"
                    if paused_run.status != target:
                        transition_agent_run(
                            pause_db,
                            paused_run,
                            target,
                            event_type="run_cancelled" if target == "cancelled" else "run_paused",
                            payload={"reason": "worker interrupted"},
                        )
                    pause_db.commit()
            finally:
                pause_db.close()
            raise
        except Exception as exc:
            logger.exception("NoteThread worker failed: %s", exc)
            failure_db = worker_session_factory()
            try:
                failed_run = get_agent_run(failure_db, str(run.id), tenant_id)
                if failed_run and failed_run.status not in {"completed", "failed", "cancelled"}:
                    target = "cancelled" if failed_run.cancel_requested else "failed"
                    transition_agent_run(
                        failure_db,
                        failed_run,
                        target,
                        event_type="run_cancelled" if target == "cancelled" else "run_failed",
                        payload={"error": str(exc)[:1000]},
                    )
                    failure_db.commit()
            finally:
                failure_db.close()
        finally:
            unregister_run_task(str(run.id), worker_task)
            worker_db.close()

    worker_task = asyncio.create_task(consume_run(), name=f"note-agent-{run.id}")
    register_run_task(str(run.id), worker_task)

    async def durable_stream():
        async for replay_event in replay_agent_run_stream(
            str(run.id),
            tenant_id,
            session_factory=worker_session_factory,
        ):
            yield json.dumps(replay_event, default=str) + "\n"

    return StreamingResponse(
        durable_stream(),
        media_type="application/x-ndjson",
        headers={
            "X-Agent-Run-ID": str(run.id),
            "X-Agent-Run-Transport": "durable-replay",
        },
    )


# Standalone Chat/Notes entry point. A thread can later be attached to a
# full Project workspace without changing its immutable cells or executions.
standalone_router = APIRouter(prefix="/api/notes", tags=["notes"])


def _get_standalone_thread(db: Session, thread_id: str, tenant_id: str) -> NoteThread:
    thread = (
        db.query(NoteThread)
        .options(selectinload(NoteThread.cells).selectinload(NoteCell.revisions).selectinload(NoteCellRevision.executions).selectinload(CellExecution.artifacts))
        .filter(
            NoteThread.id == thread_id,
            NoteThread.tenant_id == tenant_id,
            NoteThread.project_id.is_(None),
        )
        .first()
    )
    if not thread:
        raise HTTPException(status_code=404, detail="Standalone NoteThread not found")
    return thread


@standalone_router.get("", response_model=list[NoteThreadSummaryOut])
def list_standalone_note_threads(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    threads = (
        db.query(NoteThread)
        .filter(
            NoteThread.tenant_id == tenant_id,
            NoteThread.project_id.is_(None),
        )
        .order_by(NoteThread.updated_at.desc())
        .all()
    )
    return [_thread_summary_payload(thread) for thread in threads]


@standalone_router.post("", response_model=NoteThreadOut, status_code=status.HTTP_201_CREATED)
def create_standalone_note_thread(
    data: NoteThreadCreate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
    user_id: str = Depends(get_current_user_id),
):
    thread_id = str(uuid.uuid4())
    thread = NoteThread(
        id=thread_id,
        project_id=None,
        tenant_id=tenant_id,
        owner_id=user_id,
        storage_path=str(standalone_storage_path(thread_id)),
        title=data.title.strip(),
        thread_type=data.thread_type.strip().lower(),
        status="active",
    )
    db.add(thread)
    db.commit()
    db.refresh(thread)
    return _thread_payload(thread)


@standalone_router.get("/{thread_id}", response_model=NoteThreadOut)
def get_standalone_note_thread(
    thread_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    return _thread_payload(_get_standalone_thread(db, thread_id, tenant_id))


@standalone_router.patch("/{thread_id}", response_model=NoteThreadSummaryOut)
def update_standalone_note_thread(
    thread_id: str,
    data: NoteThreadUpdate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    thread = _get_standalone_thread(db, thread_id, tenant_id)
    changes = data.model_dump(exclude_unset=True)
    if "title" in changes:
        thread.title = changes["title"].strip()
    if "status" in changes:
        thread.status = changes["status"]
    if "metadata" in changes:
        thread.thread_metadata = changes["metadata"]
    db.commit()
    db.refresh(thread)
    return _thread_summary_payload(thread)


@standalone_router.delete("/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_standalone_note_thread(
    thread_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Permanently delete a standalone NoteThread and its files."""
    thread = _get_standalone_thread(db, thread_id, tenant_id)
    _delete_note_thread_files(db, thread)
    storage = standalone_storage_path(str(thread.id))
    shutil.rmtree(storage, ignore_errors=True)
    db.delete(thread)
    db.commit()


@standalone_router.post("/{thread_id}/files", status_code=status.HTTP_201_CREATED)
def upload_standalone_note_file(
    thread_id: str,
    file: UploadFile,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Attach one data file to a standalone NoteThread so the agent can inspect it."""
    from app.services.note_data import save_thread_upload

    thread = _get_standalone_thread(db, thread_id, tenant_id)
    if thread.status != "active":
        raise HTTPException(status_code=409, detail="Cannot attach files to an archived NoteThread")
    content = file.file.read(MAX_THREAD_UPLOAD_BYTES + 1)
    try:
        summary = save_thread_upload(thread, content, filename=file.filename or "upload.bin")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    thread.updated_at = _now()
    db.commit()
    return summary


@standalone_router.get("/{thread_id}/files")
def list_standalone_note_files(
    thread_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    from app.services.note_data import list_thread_data_files

    thread = _get_standalone_thread(db, thread_id, tenant_id)
    return list_thread_data_files(thread)


@standalone_router.post("/{thread_id}/datasets/import")
def import_standalone_note_dataset(
    thread_id: str,
    data: DatasetImportRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Export a known R package dataset into a standalone NoteThread's files."""
    from app.services.note_data import import_dataset_into_thread

    thread = _get_standalone_thread(db, thread_id, tenant_id)
    if thread.status != "active":
        raise HTTPException(status_code=409, detail="Cannot import into an archived NoteThread")
    try:
        result = import_dataset_into_thread(
            thread,
            package=data.package,
            dataset=data.dataset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    thread.updated_at = _now()
    db.commit()
    return result


@standalone_router.post("/{thread_id}/cells", response_model=NoteCellOut, status_code=status.HTTP_201_CREATED)
def create_standalone_note_cell(
    thread_id: str,
    data: NoteCellCreate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
    user_id: str = Depends(get_current_user_id),
):
    thread = _get_standalone_thread(db, thread_id, tenant_id)
    if thread.status != "active":
        raise HTTPException(status_code=409, detail="Cannot add cells to an archived NoteThread")

    position = data.position
    if position is None:
        current_max = (
            db.query(func.max(NoteCell.position))
            .filter(NoteCell.thread_id == thread_id)
            .scalar()
        )
        position = (current_max if current_max is not None else -1) + 1

    cell = NoteCell(thread_id=thread_id, position=position, status="active")
    cell.revisions.append(
        NoteCellRevision(
            revision=1,
            cell_type=normalise_cell_type(data.cell_type),
            language=data.language,
            content=data.content,
            revision_metadata=data.metadata,
            created_by=user_id,
        )
    )
    thread.updated_at = _now()
    db.add(cell)
    db.commit()
    db.refresh(cell)
    return _cell_payload(cell)


@standalone_router.get("/{thread_id}/cells/{cell_id}", response_model=NoteCellOut)
def get_standalone_note_cell(
    thread_id: str,
    cell_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    _get_standalone_thread(db, thread_id, tenant_id)
    return _cell_payload(_get_cell(db, thread_id, cell_id))


@standalone_router.post(
    "/{thread_id}/cells/{cell_id}/revisions",
    response_model=NoteCellRevisionOut,
    status_code=status.HTTP_201_CREATED,
)
def append_standalone_note_cell_revision(
    thread_id: str,
    cell_id: str,
    data: NoteCellRevisionCreate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
    user_id: str = Depends(get_current_user_id),
):
    thread = _get_standalone_thread(db, thread_id, tenant_id)
    if thread.status != "active":
        raise HTTPException(status_code=409, detail="Cannot revise an archived NoteThread")
    cell = _get_cell(db, thread_id, cell_id)
    if cell.status != "active":
        raise HTTPException(status_code=409, detail="Cannot revise an inactive NoteCell")

    for _ in range(2):
        latest = (
            db.query(NoteCellRevision)
            .filter(NoteCellRevision.cell_id == cell_id)
            .order_by(NoteCellRevision.revision.desc())
            .first()
        )
        revision = NoteCellRevision(
            cell_id=cell_id,
            revision=(latest.revision if latest else 0) + 1,
            cell_type=normalise_cell_type(data.cell_type),
            language=data.language,
            content=data.content,
            revision_metadata=data.metadata,
            created_by=user_id,
        )
        cell.updated_at = _now()
        thread.updated_at = cell.updated_at
        db.add(revision)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        db.refresh(revision)
        return _revision_payload(revision)

    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Concurrent cell revision detected; retry the request",
    )


@standalone_router.post("/{thread_id}/attach", response_model=NoteThreadOut)
def attach_standalone_note_thread(
    thread_id: str,
    data: NoteThreadAttach,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    thread = _get_standalone_thread(db, thread_id, tenant_id)
    project = get_project_for_tenant(db, str(data.project_id), tenant_id)
    thread.project_id = str(project.id)
    thread.updated_at = _now()
    db.commit()
    db.refresh(thread)
    _publish_note_event(str(project.id), thread_id, "note_thread_attached")
    return _thread_payload(thread)


@standalone_router.post("/{thread_id}/workspace")
def create_workspace_from_note_thread(
    thread_id: str,
    data: NoteThreadWorkspaceCreate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
    user_id: str = Depends(get_current_user_id),
):
    thread = _get_standalone_thread(db, thread_id, tenant_id)
    from app.services.study_manifest import build_study_manifest

    project = Project(
        name=(data.name or thread.title).strip() or "New analysis workspace",
        question=data.question,
        notes=data.notes,
        auto_build=data.auto_build,
        owner_id=user_id,
        tenant_id=tenant_id,
        study_manifest=build_study_manifest([]),
        agent_state="idle",
    )
    db.add(project)
    db.flush()
    thread.project_id = str(project.id)
    thread.updated_at = _now()
    db.commit()
    db.refresh(project)
    db.refresh(thread)

    from app.services.agent_runtime import record_agent_action, refresh_project_memory
    refresh_project_memory(db, project)
    record_agent_action(
        db,
        project,
        "workspace",
        "completed",
        "Workspace created from NoteThread",
        {"note_thread_id": str(thread.id)},
    )
    return {
        "project_id": str(project.id),
        "note_thread": _thread_payload(thread),
    }
