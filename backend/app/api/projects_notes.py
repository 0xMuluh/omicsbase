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
from app.models.project import Project, UploadedFile
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
        "idempotency_key": execution.idempotency_key,
        "cache_hit": bool(execution.cache_hit),
        "cache_source_execution_id": execution.cache_source_execution_id,
    }


def _note_execution_observation_payload(execution_payload: dict[str, Any], cell_payload: dict[str, Any], *, turn_id: str) -> dict[str, Any]:
    """Shape an execution result without treating queued work as success."""
    status = str((execution_payload or {}).get("status") or "unknown").lower()
    metadata = (execution_payload or {}).get("result_metadata") or {}
    if status == "completed":
        observation_status = "ok"
    elif status in {"queued", "running", "cancel_requested"}:
        observation_status = "pending"
    else:
        observation_status = "error"
    stderr = str(
        metadata.get("stderr_preview")
        or (execution_payload or {}).get("error")
        or ""
    )[:4000]
    return {
        "status": observation_status,
        "stdout": str(metadata.get("stdout_preview") or "")[:4000],
        "stderr": stderr,
        "summary": {
            "execution_status": status,
            "output_chars": metadata.get("output_chars", 0),
            "output_truncated": bool(metadata.get("output_truncated")),
            "had_errors": bool(metadata.get("had_errors")) or status in {"failed", "timed_out", "cancelled", "completed_with_errors"},
        },
        "cell": cell_payload,
        "execution": execution_payload,
        "turn_id": turn_id,
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

    from app.services.edit_engine import (
        EditBusy,
        EditConflict,
        EditEngineError,
        EditOperation,
        EditPolicy,
        apply_transaction,
        sha256_bytes,
    )

    existing_bytes = source_path.read_bytes() if source_path.is_file() else None
    operation = EditOperation(
        path=relative_source.as_posix(),
        kind="rewrite" if existing_bytes is not None else "create",
        content=content,
        base_sha256=sha256_bytes(existing_bytes),
        reason="Export NoteThread as draft Quarto source",
    )
    try:
        edit_result = apply_transaction(
            base,
            [operation],
            origin="note_report_export",
            summary=f"Export NoteThread report {relative_source.as_posix()}",
            policy=EditPolicy(
                allowed_extensions=frozenset({".qmd"}),
                allow_create=True,
                allow_delete=False,
                require_base_for_rewrite=True,
            ),
            validate=True,
            lock_timeout=0,
        )
    except EditBusy as exc:
        raise HTTPException(status_code=423, detail=exc.to_dict()) from exc
    except EditConflict as exc:
        raise HTTPException(status_code=409, detail=exc.to_dict()) from exc
    except EditEngineError as exc:
        raise HTTPException(status_code=400, detail=exc.to_dict()) from exc

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
    from app.services.project_edit_index import record_project_edit

    record_project_edit(db, project, edit_result)
    record_agent_action(
        db,
        project,
        "report",
        "completed",
        "Exported NoteThread as draft Quarto source",
        {
            "note_thread_id": str(thread.id),
            "report_id": str(existing.id),
            "transaction_id": edit_result.transaction_id,
        },
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
    """Promote only an immutable, successfully executed notebook revision."""
    from app.services.edit_engine import (
        EditConflict,
        EditEngineError,
        EditOperation,
        EditPolicy,
        apply_transaction,
        is_path_locked,
        sha256_bytes,
    )

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

    cell_id = str(arguments.get("cell_id") or "").strip()
    revision_id = str(arguments.get("revision_id") or "").strip()
    execution_id = str(arguments.get("execution_id") or "").strip()
    relative_path = str(arguments.get("path") or "").strip().replace("\\", "/")
    if relative_path.startswith("code/"):
        relative_path = relative_path[5:]
    if not relative_path:
        return {
            "status": "error",
            "error": "Promotion requires cell_id, revision_id, execution_id, and a code-relative path.",
            "turn_id": turn_id,
        }
    path_parts = Path(relative_path).parts
    if Path(relative_path).is_absolute() or ".." in path_parts:
        return {"status": "error", "error": "The path escapes the project code directory.", "turn_id": turn_id}
    if not relative_path.lower().endswith((".r", ".qmd", ".md")):
        return {"status": "error", "error": "The promotion path must stay inside code/ and use .R, .qmd, or .md.", "turn_id": turn_id}
    project_relative = f"code/{relative_path}"
    target = (base / project_relative).resolve(strict=False)
    try:
        target.relative_to(base)
    except ValueError:
        return {"status": "error", "error": "The path escapes the project code directory.", "turn_id": turn_id}
    strategy = str(arguments.get("strategy") or "create_only").strip().lower()
    supplied_base_sha256 = str(arguments.get("base_sha256") or "").strip().strip('"')
    if strategy not in {"replace", "append", "create_only"}:
        return {"status": "error", "error": "Promotion strategy must be replace, append, or create_only.", "turn_id": turn_id}
    if is_path_locked(base, project_relative):
        return {"status": "error", "error": "The promotion path is locked by workspace policy.", "turn_id": turn_id}
    if not cell_id or not revision_id or not execution_id:
        return {
            "status": "error",
            "error": "Promotion requires cell_id, revision_id, execution_id, and a code-relative path.",
            "turn_id": turn_id,
        }

    cell = db.query(NoteCell).filter(NoteCell.id == cell_id, NoteCell.thread_id == thread.id).first()
    revision = db.query(NoteCellRevision).filter(NoteCellRevision.id == revision_id, NoteCellRevision.cell_id == cell_id).first()
    execution = db.query(CellExecution).filter(CellExecution.id == execution_id, CellExecution.revision_id == revision_id).first()
    if cell is None or revision is None or execution is None:
        return {"status": "error", "error": "The supplied notebook provenance does not belong to this thread.", "turn_id": turn_id}
    if str(revision.cell_type or "") != "code" or str(revision.language or "r").lower() not in {"r", "rscript"}:
        return {"status": "error", "error": "Only successfully executed R code cells can be promoted.", "turn_id": turn_id}
    if execution.status != "completed":
        return {"status": "error", "error": f"The referenced execution is not successful (status: {execution.status}).", "turn_id": turn_id}
    expected_input = input_fingerprint(revision.content, revision.language, execution.parameters)
    if not execution.input_fingerprint or execution.input_fingerprint != expected_input:
        return {"status": "error", "error": "Execution provenance does not match the immutable cell revision.", "turn_id": turn_id}

    content = str(revision.content or "")
    if not content.strip():
        return {"status": "error", "error": "The tested cell is empty and cannot be promoted.", "turn_id": turn_id}
    if len(content.encode("utf-8")) > 200_000:
        return {"status": "error", "error": "The promoted content exceeds the 200 KB limit.", "turn_id": turn_id}
    existing = target.read_bytes() if target.exists() else None
    actual_base_sha256 = sha256_bytes(existing)
    if existing is not None and strategy == "create_only":
        return {"status": "error", "error": f"{project_relative} already exists; choose append or replace with an explicit base_sha256.", "turn_id": turn_id}
    if existing is not None and not supplied_base_sha256:
        return {
            "status": "error",
            "error": "Updating an existing promoted file requires base_sha256 from the current workspace.",
            "code": "edit_precondition_required",
            "actual_sha256": actual_base_sha256,
            "turn_id": turn_id,
        }
    if existing is not None and supplied_base_sha256 != actual_base_sha256:
        return {
            "status": "error",
            "error": "The promoted target changed since it was inspected; reload it before promotion.",
            "code": "edit_conflict",
            "expected_sha256": supplied_base_sha256,
            "actual_sha256": actual_base_sha256,
            "turn_id": turn_id,
        }
    if existing is None:
        operation = EditOperation(path=project_relative, kind="create", content=content, reason=f"Promote note execution {execution_id}")
    elif strategy == "append":
        operation = EditOperation(path=project_relative, kind="replace", search="", replace=content, base_sha256=supplied_base_sha256, reason=f"Promote note execution {execution_id}")
    else:
        operation = EditOperation(path=project_relative, kind="rewrite", content=content, base_sha256=supplied_base_sha256, reason=f"Promote note execution {execution_id}")

    try:
        result = apply_transaction(
            base,
            [operation],
            origin="note_promotion",
            summary=f"Promote tested note cell to {project_relative}",
            validate=True,
            policy=EditPolicy(
                allowed_extensions=frozenset({".r", ".qmd", ".md"}),
                allow_create=True,
                allow_delete=False,
                require_base_for_rewrite=True,
            ),
            lock_timeout=0,
        )
    except EditConflict as exc:
        return {"status": "error", "error": str(exc), "code": exc.code, "details": exc.details, "turn_id": turn_id}
    except EditEngineError as exc:
        return {"status": "error", "error": str(exc), "code": exc.code, "details": exc.details, "turn_id": turn_id}

    from app.services.agent_runtime import record_agent_action, refresh_project_memory
    from app.services.project_edit_index import record_project_edit
    record_project_edit(db, project, result)
    refresh_project_memory(db, project)
    record_agent_action(
        db,
        project,
        "file_edit",
        "completed",
        f"Promoted tested note cell to {project_relative}",
        {
            "transaction_id": result.transaction_id,
            "note_thread_id": str(thread.id),
            "cell_id": cell_id,
            "revision_id": revision_id,
            "execution_id": execution_id,
            "strategy": strategy,
        },
        files=[project_relative],
    )
    return {
        "status": "ok",
        "path": project_relative,
        "promoted": True,
        "turn_id": turn_id,
        "transaction_id": result.transaction_id,
        "cell_id": cell_id,
        "revision_id": revision_id,
        "execution_id": execution_id,
        "content_sha256": sha256_bytes(content.encode("utf-8")),
    }

def _note_agent_context(db: Session, thread: NoteThread) -> dict[str, Any]:
    cells: list[dict[str, Any]] = []
    ordered_cells = sorted(thread.cells, key=lambda item: (int(item.position or 0), item.created_at))
    for cell in ordered_cells[-12:]:
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
    from app.services.intent_fastpath import is_demonstration_request
    demonstration_request = is_demonstration_request(message)
    if not message:
        raise HTTPException(status_code=422, detail="The notebook message cannot be blank")

    thread = _get_note_thread_for_tenant(db, thread_id, tenant_id)
    if thread.status != "active":
        raise HTTPException(status_code=409, detail="Cannot add turns to an archived NoteThread")
    from app.services.agent_plans import (
        append_continuation_step,
        continuation_can_resume,
        continuation_prompt,
        get_continuation_plan,
        mark_continuation_running,
        mark_continuation_consumed,
    )
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
        TOKEN_CHUNK_FLUSH_CHARS,
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

    continuation_plan = get_continuation_plan(run)
    continuation_resume = (
        not run_created
        and continuation_can_resume(run)
        and not is_run_task_active(str(run.id))
    )
    resume_existing_run = (
        not run_created
        and not is_run_task_active(str(run.id))
        and bool(run.resumable)
        and (continuation_resume or (run.status == "paused" and not continuation_plan))
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
        if continuation_resume and continuation_plan:
            mark_continuation_running(run)
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
            metadata={
                "turn_id": turn_id,
                "role": "user",
                "attachments": [a.model_dump() for a in data.attachments] if data.attachments else [],
            },
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
    agent_message = continuation_prompt(continuation_plan) if continuation_resume and continuation_plan else message
    generated_code_cells = 0
    generated_note_cells = 0
    knowledge_sources: list[str] = []

    async def _execution_observation(
        execution_payload: dict,
        cell_payload: dict,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        """Wait once and shape a durable execution result for the Note agent."""
        if (
            settings.note_execution_agent_wait_enabled
            and str(execution_payload.get("status") or "")
            in {"queued", "running", "cancel_requested"}
        ):
            execution_payload = await _wait_for_note_execution(
                db,
                str(execution_payload["id"]),
                timeout_seconds,
                cancel_check=lambda: run_cancel_requested(db, str(run.id)),
            )
        return _note_execution_observation_payload(execution_payload, cell_payload, turn_id=turn_id)

    def knowledge_search_handler(arguments: dict) -> dict:
        from app.services.bioc_knowledge import search_bioc_knowledge

        query = str(arguments.get("query") or message).strip()
        channel = str(arguments.get("channel") or "stable").strip().lower()
        try:
            limit = int(arguments.get("limit") or 3)
        except (TypeError, ValueError):
            limit = 3
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
        return result

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

        # A resumed/replayed turn must not append the same generated cell and
        # execute it again. The AgentRun id is the durable turn identity; the
        # immutable revision content is the call identity.
        for existing_cell in fresh_thread.cells:
            for existing_revision in existing_cell.revisions:
                metadata = existing_revision.revision_metadata or {}
                if (
                    str(metadata.get("turn_id") or "") == turn_id
                    and existing_revision.cell_type == "code"
                    and str(existing_revision.content or "").strip() == code
                ):
                    prior_executions = list(existing_revision.executions or [])
                    if prior_executions:
                        prior_execution = max(
                            prior_executions,
                            key=lambda item: item.created_at,
                        )
                        replay = await _execution_observation(
                            _execution_payload(prior_execution),
                            _cell_payload(existing_cell),
                            timeout_seconds,
                        )
                        replay["duplicate_call"] = True
                        return replay

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
            idempotency_key=(
                "agent:"
                + turn_id
                + ":"
                + hashlib.sha256(
                    (code + json.dumps(parameters, sort_keys=True, default=str)).encode("utf-8")
                ).hexdigest()[:48]
            ),
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
        return await _execution_observation(execution_payload, cell_payload, timeout_seconds)

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
        pending_async_execution = False
        waiting_for_dependency = False
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
                message=agent_message,
                cells=prior_cells,
                context=context,
                action_handler=action_handler,
                knowledge_search_handler=knowledge_search_handler,
                cancel_check=lambda: run_cancel_requested(db, str(run.id)),
            ):
                output_event = dict(event)
                output_event["turn_id"] = turn_id
                event_type = str(output_event.get("type") or "stream_event")
                if event_type == "usage":
                    usage = output_event.get("usage")
                    if isinstance(usage, dict):
                        for key, value in usage.items():
                            usage_key = str(key)
                            try:
                                provider_usage[usage_key] = provider_usage.get(usage_key, 0) + int(value)
                            except (TypeError, ValueError):
                                continue
                    continue
                if event_type == "token":
                    token = str(output_event.get("token") or "")
                    output_chars += len(token)
                    if token:
                        token_buffer.append(token)
                        if sum(len(item) for item in token_buffer) >= TOKEN_CHUNK_FLUSH_CHARS:
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
                elif event_type == "wait":
                    waiting_for_dependency = True
                    if run.status in {"running", "waiting_tool"}:
                        transition_agent_run(
                            db,
                            run,
                            "paused",
                            event_type="run_waiting",
                            payload={"dependency": output_event.get("dependency"), "step": output_event.get("step")},
                        )
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
                if output_event.get("type") == "execution_queued" and isinstance(output_event.get("execution"), dict):
                    pending_async_execution = True
                    execution = output_event["execution"]
                    execution_id = execution.get("id")
                    if execution_id:
                        append_continuation_step(
                            run,
                            action="run_r_cell",
                            dependency_kind="execution",
                            dependency_id=str(execution_id),
                            instruction=str(message),
                            arguments=output_event.get("tool_arguments") if isinstance(output_event.get("tool_arguments"), dict) else {},
                            dependency_status=str(execution.get("status") or "queued"),
                        )

                continuation = get_continuation_plan(run)
                if (
                    output_event.get("type") == "final"
                    and pending_async_execution
                    and not continuation_resume
                    and continuation
                    and (
                        continuation.get("status") == "waiting"
                        or (
                            not settings.note_execution_agent_wait_enabled
                            and continuation.get("status") in {"ready", "failed"}
                        )
                    )
                ):
                    output_event["continuation_pending"] = True

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
                    continuation = get_continuation_plan(run)
                    waiting_for_continuation = (
                        waiting_for_dependency
                        or (
                            event_type == "final"
                            and bool(continuation)
                            and continuation.get("status") == "waiting"
                        )
                    )
                    if (
                        event_type == "final"
                        and pending_async_execution
                        and not continuation_resume
                        and not settings.note_execution_agent_wait_enabled
                        and bool(continuation)
                        and continuation.get("status") in {"ready", "failed"}
                    ):
                        waiting_for_continuation = True
                    if (
                        not cancelled
                        and not waiting_for_continuation
                        and continuation
                        and continuation.get("status") in {"ready", "failed", "running"}
                    ):
                        consumed = mark_continuation_consumed(run)
                        if consumed:
                            continuation = consumed
                            waiting_for_continuation = continuation.get("status") in {
                                "waiting",
                                "ready",
                                "failed",
                                "running",
                            }
                            record_stream_event(
                                db,
                                run,
                                {
                                    "type": "continuation_consumed",
                                    "action": consumed.get("action"),
                                    "step_id": consumed.get("active_step_id"),
                                    "continuation_status": consumed.get("status"),
                                },
                            )
                    if (
                        event_type == "final"
                        and not cancelled
                        and not waiting_for_continuation
                        and demonstration_request
                        and not knowledge_sources
                        and generated_code_cells == 0
                        and generated_note_cells == 0
                    ):
                        logger.warning(
                            "note_demonstration_completed_without_grounding",
                            extra={
                                "turn_id": turn_id,
                                "thread_id": thread_id,
                                "knowledge_sources": 0,
                                "generated_code_cells": generated_code_cells,
                                "generated_note_cells": generated_note_cells,
                            },
                        )
                    target_status = "cancelled" if cancelled else ("paused" if waiting_for_continuation else "completed")
                    if run.status not in {"completed", "failed", "cancelled"}:
                        transition_agent_run(
                            db,
                            run,
                            target_status,
                            event_type=(
                                "run_cancelled"
                                if cancelled
                                else "run_waiting_continuation"
                                if waiting_for_continuation
                                else "run_completed"
                            ),
                            payload={"cell_id": str((output_event.get("cell") or {}).get("id")) if isinstance(output_event.get("cell"), dict) else None},
                        )
                    run.result_payload = {"cell_id": (output_event.get("cell") or {}).get("id") if isinstance(output_event.get("cell"), dict) else None, "event_type": event_type}
                    if not telemetry_written:
                        record_run_telemetry(
                            db,
                            run,
                            kind="agent",
                            operation="note_turn",
                            status=(
                                "cancelled"
                                if cancelled
                                else "paused"
                                if waiting_for_continuation
                                else "completed"
                            ),
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
            resume_db = worker_session_factory()
            try:
                from app.services.agent_continuations import dispatch_ready_continuations

                dispatch_ready_continuations(resume_db, run_id=str(run.id))
            finally:
                resume_db.close()
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


def _note_thread_question(thread: NoteThread) -> str | None:
    for cell in sorted(thread.cells, key=lambda item: (int(item.position or 0), item.created_at)):
        revision = cell.revisions[-1] if cell.revisions else None
        if revision is None:
            continue
        if str(revision.cell_type or "") in {"agent", "markdown"} and str(revision.content or "").strip():
            return str(revision.content).strip()[:20_000]
    return None


def _note_thread_planning_notes(thread: NoteThread) -> str:
    parts = [f"Imported from NoteThread {thread.id}; immutable notebook cells and execution provenance remain attached."]
    for cell in sorted(thread.cells, key=lambda item: (int(item.position or 0), item.created_at))[-24:]:
        revision = cell.revisions[-1] if cell.revisions else None
        if revision is None:
            continue
        content = str(revision.content or "").strip()
        if not content:
            continue
        if str(revision.cell_type or "") in {"agent", "markdown", "provenance"}:
            parts.append(content[:2000])
        for execution in sorted(revision.executions or [], key=lambda item: item.created_at, reverse=True)[:1]:
            if execution.status == "completed":
                preview = str((execution.result_metadata or {}).get("stdout_preview") or "").strip()
                if preview:
                    parts.append(f"Observed successful execution for cell {cell.id}: {preview[:1200]}")
    return "\n\n".join(parts)[:20_000]


@standalone_router.post("/{thread_id}/workspace")
def create_workspace_from_note_thread(
    thread_id: str,
    data: NoteThreadWorkspaceCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
    user_id: str = Depends(get_current_user_id),
):
    """Create a workspace while carrying note files and findings forward."""
    thread = _get_standalone_thread(db, thread_id, tenant_id)
    from app.services.file_inspector import inspect_file
    from app.services.study_manifest import build_study_manifest

    question = (data.question or _note_thread_question(thread) or "").strip() or None
    imported_notes = _note_thread_planning_notes(thread)
    notes = "\n\n".join(part for part in [data.notes, imported_notes] if part and part.strip())[:20_000] or None
    project = Project(
        name=(data.name or thread.title).strip() or "New analysis workspace",
        name_source="user",
        question=question,
        notes=notes,
        auto_build=data.auto_build,
        owner_id=user_id,
        tenant_id=tenant_id,
        study_manifest=build_study_manifest([]),
        agent_state="idle",
    )
    db.add(project)
    db.flush()
    project_dir = Path(settings.projects_dir).resolve() / str(project.id)
    project_dir.mkdir(parents=True, exist_ok=True)
    project.project_dir = str(project_dir)

    standalone_root = Path(thread.storage_path).resolve() if thread.storage_path else None
    if standalone_root and standalone_root.exists():
        source_meta = standalone_root / ".omicsbase"
        if source_meta.is_dir():
            shutil.copytree(source_meta, project_dir / ".omicsbase", dirs_exist_ok=True)
        source_uploads = standalone_root / "uploads"
        destination_note_uploads = project_dir / ".omicsbase" / "note-uploads" / str(thread.id)
        destination_note_uploads.mkdir(parents=True, exist_ok=True)
        data_dir = project_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        imported_records = []
        if source_uploads.is_dir():
            for source in sorted(source_uploads.iterdir()):
                if not source.is_file():
                    continue
                note_destination = destination_note_uploads / source.name
                data_destination = data_dir / source.name
                shutil.copy2(source, note_destination)
                shutil.copy2(source, data_destination)
                summary = inspect_file(str(data_destination))
                imported_records.append(
                    UploadedFile(
                        project_id=str(project.id),
                        file_role="other",
                        original_name=source.name,
                        detected_format=summary.get("format"),
                        file_summary=summary,
                        file_path=str(data_destination),
                    )
                )
        if imported_records:
            db.add_all(imported_records)
            db.flush()
            project.study_manifest = build_study_manifest(imported_records)

    # Persist an explicit, hash-addressed transfer manifest. The generated
    # workspace may later be rebuilt, but this record keeps the notebook
    # question, immutable revisions, successful executions, and copied inputs
    # auditable as one transfer decision.
    transfer_cells = []
    for cell in sorted(thread.cells, key=lambda item: (int(item.position or 0), item.created_at)):
        revisions = []
        for revision in sorted(cell.revisions, key=lambda item: item.revision):
            revisions.append({
                "id": str(revision.id),
                "revision": int(revision.revision),
                "cell_type": revision.cell_type,
                "language": revision.language,
                "content_sha256": hashlib.sha256(str(revision.content or "").encode("utf-8")).hexdigest(),
                "executions": [
                    {
                        "id": str(execution.id),
                        "status": execution.status,
                        "input_fingerprint": execution.input_fingerprint,
                        "environment_fingerprint": execution.environment_fingerprint,
                        "artifact_count": len(execution.artifacts or []),
                    }
                    for execution in sorted(revision.executions, key=lambda item: item.created_at)
                ],
            })
        transfer_cells.append({"id": str(cell.id), "position": int(cell.position or 0), "revisions": revisions})
    transfer_manifest = {
        "schema_version": "1.0",
        "source_thread_id": str(thread.id),
        "source_thread_title": thread.title,
        "question": question,
        "auto_build_requested": bool(data.auto_build),
        "copied_uploads": [str(file.original_name or "") for file in project.files],
        "cells": transfer_cells,
        "created_at": _now().isoformat(),
    }
    transfer_bytes = json.dumps(transfer_manifest, indent=2, sort_keys=True, default=str).encode("utf-8")
    transfer_manifest["sha256"] = hashlib.sha256(transfer_bytes).hexdigest()
    transfer_dir = project_dir / ".omicsbase"
    transfer_dir.mkdir(parents=True, exist_ok=True)
    transfer_path = transfer_dir / "note-transfer-manifest.json"
    temporary_transfer = transfer_path.with_suffix(".json.tmp")
    temporary_transfer.write_text(json.dumps(transfer_manifest, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temporary_transfer.replace(transfer_path)
    manifest_snapshot = dict(project.study_manifest or {})
    manifest_snapshot["note_transfer"] = {
        "source_thread_id": str(thread.id),
        "manifest_path": ".omicsbase/note-transfer-manifest.json",
        "sha256": transfer_manifest["sha256"],
        "cell_count": len(transfer_cells),
        "upload_count": len(project.files),
    }
    project.study_manifest = manifest_snapshot
    thread.project_id = str(project.id)
    thread.storage_path = str(project_dir)
    thread.updated_at = _now()
    db.commit()
    db.refresh(project)
    db.refresh(thread)

    from app.services.agent_runtime import record_agent_action, refresh_project_memory
    refresh_project_memory(db, project, files=list(project.files))
    record_agent_action(
        db,
        project,
        "workspace",
        "completed",
        "Workspace created from NoteThread with files and findings carried forward",
        {
            "note_thread_id": str(thread.id),
            "imported_file_count": len(project.files),
            "cell_count": len(thread.cells),
            "question_carried_forward": bool(question),
        },
    )

    # Auto-build is explicit in the review wizard. Do not silently claim that
    # it happened: only queue planning when the transfer actually carried
    # study inputs, and report a durable reason when it cannot start.
    auto_build_job = None
    auto_build_reason = None
    if data.auto_build:
        if not project.files:
            auto_build_reason = "Auto-build was requested, but the NoteThread has no uploaded study data."
        else:
            try:
                from app.api.projects_pipeline import _dispatch_task
                from app.models.project import Job
                from app.services.agent_runtime import record_agent_action, set_agent_state
                from app.tasks.analysis import run_planning

                auto_build_job = Job(project_id=str(project.id), job_type="plan", status="pending")
                db.add(auto_build_job)
                project.status = "planning"
                db.commit()
                db.refresh(auto_build_job)
                set_agent_state(db, project, "planning", "Planning transferred NoteThread inputs")
                record_agent_action(
                    db,
                    project,
                    "plan",
                    "started",
                    "Planning transferred NoteThread inputs",
                    {"note_thread_id": str(thread.id), "auto_build": True},
                    job_id=str(auto_build_job.id),
                )
                _dispatch_task(
                    run_planning,
                    project,
                    auto_build_job,
                    db,
                    background_tasks,
                )
            except Exception as exc:
                auto_build_reason = f"Auto-build could not be queued: {str(exc)[:500]}"
                if auto_build_job is not None:
                    auto_build_job.status = "failed"
                    auto_build_job.error = auto_build_reason
                project.status = "failed"
                db.commit()

    return {
        "project_id": str(project.id),
        "note_thread": _thread_payload(thread),
        "carried_forward": {
            "files": len(project.files),
            "cells": len(thread.cells),
            "question": question,
            "notes": bool(notes),
            "manifest": {
                "path": ".omicsbase/note-transfer-manifest.json",
                "sha256": transfer_manifest["sha256"],
                "cell_count": len(transfer_cells),
                "upload_count": len(project.files),
            },
            "auto_build": {
                "requested": bool(data.auto_build),
                "queued": auto_build_job is not None and auto_build_reason is None,
                "job_id": str(auto_build_job.id) if auto_build_job is not None else None,
                "reason": auto_build_reason,
            },
        },
    }
