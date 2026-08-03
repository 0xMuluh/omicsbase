"""Tenant-scoped isolated execution endpoints for NoteThread code cells."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import get_current_tenant, get_project_for_tenant
from app.config import settings
from app.database import get_db
from app.models.notes import CellExecution, NoteCell, NoteCellRevision, NoteExecutionArtifact, NoteExecutionEvent, NoteThread
from app.schemas.schemas import NoteCellExecutionCreate, NoteCellExecutionOut, NoteExecutionEventOut
from app.services.note_execution import (
    SUPPORTED_NOTE_LANGUAGES,
    environment_fingerprint,
    input_fingerprint,
    normalise_note_language,
)
from app.services.note_execution_events import append_execution_event, lock_execution
from app.services.note_scope import thread_storage_path
from app.services.note_execution_cache import (
    dependency_fingerprint,
    execution_cache_key,
    find_reusable_execution,
    resolve_upstream_executions,
    reuse_cached_execution,
)
from app.api.projects_notes import (
    _event_payload,
    _execution_payload,
    _get_cell,
    _get_execution,
    _get_thread,
    _get_standalone_thread,
    _publish_execution_event,
)

router = APIRouter()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_revision(cell, requested_revision: int | None) -> NoteCellRevision:
    if requested_revision is None:
        revision = cell.revisions[-1] if cell.revisions else None
    else:
        revision = next(
            (item for item in cell.revisions if item.revision == requested_revision),
            None,
        )
    if not revision:
        raise HTTPException(status_code=404, detail="Requested cell revision not found")
    return revision


def _create_execution(
    db: Session,
    *,
    revision_id: str,
    timeout_seconds: int,
    parameters: dict,
    environment: str,
    input_hash: str,
    cache_policy: str,
    cache_key: str,
    dependency_hash: str,
    upstream_execution_ids: list[str],
) -> CellExecution:
    for _ in range(3):
        latest_attempt = (
            db.query(func.max(CellExecution.attempt))
            .filter(CellExecution.revision_id == revision_id)
            .scalar()
        )
        execution = CellExecution(
            revision_id=revision_id,
            attempt=(latest_attempt or 0) + 1,
            status="queued",
            execution_kind="isolated",
            timeout_seconds=timeout_seconds,
            cancel_requested=False,
            environment_fingerprint=environment,
            input_fingerprint=input_hash,
            parameters=parameters,
            cache_policy=cache_policy,
            cache_key=cache_key,
            dependency_fingerprint=dependency_hash,
            upstream_execution_ids=upstream_execution_ids,
        )
        db.add(execution)
        try:
            db.flush()
            append_execution_event(
                db,
                execution,
                "note_execution_queued",
                {
                    "cache_policy": cache_policy,
                    "cache_key": cache_key,
                    "dependency_fingerprint": dependency_hash,
                },
            )
            db.commit()
            db.refresh(execution)
            return execution
        except IntegrityError:
            db.rollback()
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Concurrent execution attempt allocation failed; retry the request",
    )


def _dispatch(execution_id: str, project_id: str, background_tasks: BackgroundTasks) -> None:
    from app.tasks.notes import run_note_cell_execution

    backend = settings.task_backend.lower()
    if backend == "celery":
        run_note_cell_execution.delay(str(project_id), str(execution_id))
        return
    if backend == "background":
        background_tasks.add_task(run_note_cell_execution, str(project_id), str(execution_id))
        return
    raise RuntimeError(f"Unsupported task backend: {settings.task_backend}")


@router.post(
    "/{project_id}/notes/{thread_id}/cells/{cell_id}/execute",
    response_model=NoteCellExecutionOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def execute_note_cell(
    project_id: str,
    thread_id: str,
    cell_id: str,
    data: NoteCellExecutionCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Queue execution of a persisted R code revision, never request-body code."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    thread = _get_thread(db, project_id, thread_id)
    if thread.status != "active":
        raise HTTPException(status_code=409, detail="Cannot execute an archived NoteThread")
    cell = _get_cell(db, thread_id, cell_id)
    if cell.status != "active":
        raise HTTPException(status_code=409, detail="Cannot execute an inactive NoteCell")

    revision = _resolve_revision(cell, data.revision)
    if revision.cell_type != "code":
        raise HTTPException(status_code=409, detail="Only code cells can be executed")
    language = normalise_note_language(revision.language)
    if language not in SUPPORTED_NOTE_LANGUAGES:
        raise HTTPException(
            status_code=422,
            detail={"message": "Unsupported note-cell language", "supported": sorted(SUPPORTED_NOTE_LANGUAGES)},
        )
    if not revision.content.strip():
        raise HTTPException(status_code=422, detail="Cannot execute an empty code cell")
    if data.cache_policy == "reuse" and not settings.note_execution_cache_enabled:
        raise HTTPException(status_code=409, detail="Execution cache reuse is disabled by the server")
    # Cells share one persistent workspace per thread, so a cached result is
    # not reproducible: reuse would silently return stale output.
    effective_cache_policy = "off" if settings.note_execution_shared_workspace else data.cache_policy

    timeout_seconds = data.timeout_seconds or int(settings.note_execution_default_timeout_seconds)
    max_timeout = int(settings.note_execution_max_timeout_seconds)
    if timeout_seconds > max_timeout:
        raise HTTPException(
            status_code=422,
            detail=f"timeout_seconds cannot exceed {max_timeout}",
        )

    parameters = dict(data.parameters or {})
    input_hash = input_fingerprint(revision.content, language, parameters)
    environment_hash = environment_fingerprint(language)
    upstream_execution_ids = [str(item) for item in data.upstream_execution_ids]
    try:
        upstream_executions = resolve_upstream_executions(
            db,
            project_id,
            upstream_execution_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    dependency_hash = dependency_fingerprint(upstream_executions)
    cache_key = execution_cache_key(
        input_fingerprint=input_hash,
        environment_fingerprint=environment_hash,
        dependency_fingerprint=dependency_hash,
        timeout_seconds=timeout_seconds,
    )
    execution = _create_execution(
        db,
        revision_id=str(revision.id),
        timeout_seconds=timeout_seconds,
        parameters=parameters,
        environment=environment_hash,
        input_hash=input_hash,
        cache_policy=effective_cache_policy,
        cache_key=cache_key,
        dependency_hash=dependency_hash,
        upstream_execution_ids=upstream_execution_ids,
    )
    _publish_execution_event(project_id, thread_id, execution, "note_execution_queued")

    if execution.cache_policy == "reuse":
        cached_execution = find_reusable_execution(
            db,
            project_id=project_id,
            cache_key=execution.cache_key,
            project_dir=str(thread_storage_path(thread, project)),
            exclude_execution_id=str(execution.id),
        )
        if cached_execution is not None:
            reuse_cached_execution(
                db,
                execution=execution,
                source=cached_execution,
            )
            append_execution_event(
                db,
                execution,
                "note_execution_cache_hit",
                {
                    "cache_source_execution_id": str(cached_execution.id),
                    "cache_key": execution.cache_key,
                },
            )
            db.commit()
            _publish_execution_event(project_id, thread_id, execution, "note_execution_cache_hit")
            return _execution_payload(execution)

    try:
        _dispatch(str(execution.id), project_id, background_tasks)
    except Exception as exc:
        execution.status = "failed"
        execution.error = f"Failed to enqueue note execution: {exc}"[:20_000]
        execution.finished_at = _now()
        append_execution_event(
            db,
            execution,
            "note_execution_failed",
            {"error": execution.error},
        )
        db.commit()
        _publish_execution_event(project_id, thread_id, execution, "note_execution_failed")
        raise HTTPException(status_code=503, detail=execution.error) from exc

    return _execution_payload(execution)


@router.get(
    "/{project_id}/notes/{thread_id}/cells/{cell_id}/executions/{execution_id}",
    response_model=NoteCellExecutionOut,
)
def get_note_cell_execution(
    project_id: str,
    thread_id: str,
    cell_id: str,
    execution_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    get_project_for_tenant(db, project_id, tenant_id)
    _get_thread(db, project_id, thread_id)
    return _execution_payload(_get_execution(db, project_id, thread_id, cell_id, execution_id))




@router.get(
    "/{project_id}/notes/{thread_id}/cells/{cell_id}/executions/{execution_id}/events",
    response_model=list[NoteExecutionEventOut],
)
def list_note_cell_execution_events(
    project_id: str,
    thread_id: str,
    cell_id: str,
    execution_id: str,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Replay ordered execution events after a client cursor."""
    get_project_for_tenant(db, project_id, tenant_id)
    _get_thread(db, project_id, thread_id)
    execution = _get_execution(db, project_id, thread_id, cell_id, execution_id)
    events = (
        db.query(NoteExecutionEvent)
        .filter(
            NoteExecutionEvent.execution_id == execution.id,
            NoteExecutionEvent.sequence > after_sequence,
        )
        .order_by(NoteExecutionEvent.sequence.asc())
        .limit(limit)
        .all()
    )
    return [_event_payload(event) for event in events]


@router.get(
    "/{project_id}/notes/{thread_id}/cells/{cell_id}/executions/{execution_id}/artifacts/{artifact_id}/content",
)
def get_note_execution_artifact_content(
    project_id: str,
    thread_id: str,
    cell_id: str,
    execution_id: str,
    artifact_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Stream a validated execution artifact without exposing workspace paths."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    thread = _get_thread(db, project_id, thread_id)
    execution = _get_execution(db, project_id, thread_id, cell_id, execution_id)
    artifact = (
        db.query(NoteExecutionArtifact)
        .filter(
            NoteExecutionArtifact.id == artifact_id,
            NoteExecutionArtifact.execution_id == execution.id,
        )
        .first()
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="Execution artifact not found")

    base = thread_storage_path(thread, project).resolve()
    relative_path = Path(artifact.relative_path)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise HTTPException(status_code=404, detail="Execution artifact path is invalid")
    artifact_path = (base / relative_path).resolve()
    if base not in artifact_path.parents or not artifact_path.is_file():
        raise HTTPException(status_code=404, detail="Execution artifact file is unavailable")
    return FileResponse(
        path=artifact_path,
        media_type=artifact.mime_type,
        filename=artifact_path.name,
    )


@router.post(
    "/{project_id}/notes/{thread_id}/cells/{cell_id}/executions/{execution_id}/cancel",
    response_model=NoteCellExecutionOut,
)
def cancel_note_cell_execution(
    project_id: str,
    thread_id: str,
    cell_id: str,
    execution_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    get_project_for_tenant(db, project_id, tenant_id)
    _get_thread(db, project_id, thread_id)
    execution = _get_execution(db, project_id, thread_id, cell_id, execution_id)
    lock_execution(db, execution)

    if execution.status in {"completed", "failed", "timed_out"}:
        raise HTTPException(status_code=409, detail=f"Cannot cancel a {execution.status} execution")
    if execution.status == "cancelled":
        return _execution_payload(execution)

    execution.cancel_requested = True
    if execution.status == "queued":
        execution.status = "cancelled"
        execution.finished_at = _now()
        event_type = "note_execution_cancelled"
    else:
        execution.status = "cancel_requested"
        event_type = "note_execution_cancel_requested"
    append_execution_event(
        db,
        execution,
        event_type,
        {"cancel_requested": True},
    )
    db.commit()
    _publish_execution_event(project_id, thread_id, execution, event_type)
    return _execution_payload(execution)



standalone_execution_router = APIRouter(prefix="/api/notes", tags=["notes"])


def _get_standalone_execution(
    db: Session,
    thread_id: str,
    cell_id: str,
    execution_id: str,
    tenant_id: str,
) -> CellExecution:
    execution = (
        db.query(CellExecution)
        .join(NoteCellRevision, NoteCellRevision.id == CellExecution.revision_id)
        .join(NoteCell, NoteCell.id == NoteCellRevision.cell_id)
        .join(NoteThread, NoteThread.id == NoteCell.thread_id)
        .filter(
            CellExecution.id == execution_id,
            NoteCell.id == cell_id,
            NoteCell.thread_id == thread_id,
            NoteThread.id == thread_id,
            NoteThread.project_id.is_(None),
            NoteThread.tenant_id == tenant_id,
        )
        .first()
    )
    if not execution:
        raise HTTPException(status_code=404, detail="Standalone cell execution not found")
    return execution


def _dispatch_standalone(execution_id: str, thread_id: str, background_tasks: BackgroundTasks) -> None:
    from app.tasks.notes import run_note_cell_execution

    backend = settings.task_backend.lower()
    if backend == "celery":
        run_note_cell_execution.delay(str(thread_id), str(execution_id), "thread")
        return
    if backend == "background":
        background_tasks.add_task(run_note_cell_execution, str(thread_id), str(execution_id), "thread")
        return
    raise RuntimeError(f"Unsupported task backend: {settings.task_backend}")


@standalone_execution_router.post(
    "/{thread_id}/cells/{cell_id}/execute",
    response_model=NoteCellExecutionOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def execute_standalone_note_cell(
    thread_id: str,
    cell_id: str,
    data: NoteCellExecutionCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    thread = _get_standalone_thread(db, thread_id, tenant_id)
    if thread.status != "active":
        raise HTTPException(status_code=409, detail="Cannot execute an archived NoteThread")
    cell = _get_cell(db, thread_id, cell_id)
    if cell.status != "active":
        raise HTTPException(status_code=409, detail="Cannot execute an inactive NoteCell")

    revision = _resolve_revision(cell, data.revision)
    if revision.cell_type != "code":
        raise HTTPException(status_code=409, detail="Only code cells can be executed")
    language = normalise_note_language(revision.language)
    if language not in SUPPORTED_NOTE_LANGUAGES:
        raise HTTPException(
            status_code=422,
            detail={"message": "Unsupported note-cell language", "supported": sorted(SUPPORTED_NOTE_LANGUAGES)},
        )
    if not revision.content.strip():
        raise HTTPException(status_code=422, detail="Cannot execute an empty code cell")
    if data.cache_policy == "reuse" and not settings.note_execution_cache_enabled:
        raise HTTPException(status_code=409, detail="Execution cache reuse is disabled by the server")
    # Cells share one persistent workspace per thread, so a cached result is
    # not reproducible: reuse would silently return stale output.
    effective_cache_policy = "off" if settings.note_execution_shared_workspace else data.cache_policy

    timeout_seconds = data.timeout_seconds or int(settings.note_execution_default_timeout_seconds)
    max_timeout = int(settings.note_execution_max_timeout_seconds)
    if timeout_seconds > max_timeout:
        raise HTTPException(status_code=422, detail=f"timeout_seconds cannot exceed {max_timeout}")

    parameters = dict(data.parameters or {})
    input_hash = input_fingerprint(revision.content, language, parameters)
    environment_hash = environment_fingerprint(language)
    upstream_execution_ids = [str(item) for item in data.upstream_execution_ids]
    try:
        upstream_executions = resolve_upstream_executions(
            db,
            None,
            upstream_execution_ids,
            thread_id=thread_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    dependency_hash = dependency_fingerprint(upstream_executions)
    cache_key = execution_cache_key(
        input_fingerprint=input_hash,
        environment_fingerprint=environment_hash,
        dependency_fingerprint=dependency_hash,
        timeout_seconds=timeout_seconds,
    )
    execution = _create_execution(
        db,
        revision_id=str(revision.id),
        timeout_seconds=timeout_seconds,
        parameters=parameters,
        environment=environment_hash,
        input_hash=input_hash,
        cache_policy=effective_cache_policy,
        cache_key=cache_key,
        dependency_hash=dependency_hash,
        upstream_execution_ids=upstream_execution_ids,
    )
    root = thread_storage_path(thread)

    if execution.cache_policy == "reuse":
        cached_execution = find_reusable_execution(
            db,
            project_id=None,
            thread_id=thread_id,
            cache_key=execution.cache_key,
            project_dir=str(root),
            exclude_execution_id=str(execution.id),
        )
        if cached_execution is not None:
            reuse_cached_execution(db, execution=execution, source=cached_execution)
            append_execution_event(
                db,
                execution,
                "note_execution_cache_hit",
                {
                    "cache_source_execution_id": str(cached_execution.id),
                    "cache_key": execution.cache_key,
                },
            )
            db.commit()
            return _execution_payload(execution)

    try:
        _dispatch_standalone(str(execution.id), thread_id, background_tasks)
    except Exception as exc:
        execution.status = "failed"
        execution.error = f"Failed to enqueue note execution: {exc}"[:20_000]
        execution.finished_at = _now()
        append_execution_event(db, execution, "note_execution_failed", {"error": execution.error})
        db.commit()
        raise HTTPException(status_code=503, detail=execution.error) from exc

    return _execution_payload(execution)


@standalone_execution_router.get(
    "/{thread_id}/cells/{cell_id}/executions/{execution_id}",
    response_model=NoteCellExecutionOut,
)
def get_standalone_note_cell_execution(
    thread_id: str,
    cell_id: str,
    execution_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    _get_standalone_thread(db, thread_id, tenant_id)
    return _execution_payload(
        _get_standalone_execution(db, thread_id, cell_id, execution_id, tenant_id)
    )


@standalone_execution_router.get(
    "/{thread_id}/cells/{cell_id}/executions/{execution_id}/events",
    response_model=list[NoteExecutionEventOut],
)
def list_standalone_note_cell_execution_events(
    thread_id: str,
    cell_id: str,
    execution_id: str,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    _get_standalone_thread(db, thread_id, tenant_id)
    execution = _get_standalone_execution(db, thread_id, cell_id, execution_id, tenant_id)
    events = (
        db.query(NoteExecutionEvent)
        .filter(
            NoteExecutionEvent.execution_id == execution.id,
            NoteExecutionEvent.sequence > after_sequence,
        )
        .order_by(NoteExecutionEvent.sequence.asc())
        .limit(limit)
        .all()
    )
    return [_event_payload(event) for event in events]


@standalone_execution_router.get(
    "/{thread_id}/cells/{cell_id}/executions/{execution_id}/artifacts/{artifact_id}/content",
)
def get_standalone_note_execution_artifact_content(
    thread_id: str,
    cell_id: str,
    execution_id: str,
    artifact_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    thread = _get_standalone_thread(db, thread_id, tenant_id)
    execution = _get_standalone_execution(db, thread_id, cell_id, execution_id, tenant_id)
    artifact = (
        db.query(NoteExecutionArtifact)
        .filter(
            NoteExecutionArtifact.id == artifact_id,
            NoteExecutionArtifact.execution_id == execution.id,
        )
        .first()
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="Execution artifact not found")

    base = thread_storage_path(thread)
    relative_path = Path(artifact.relative_path)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise HTTPException(status_code=404, detail="Execution artifact path is invalid")
    artifact_path = (base / relative_path).resolve()
    if base not in artifact_path.parents or not artifact_path.is_file():
        raise HTTPException(status_code=404, detail="Execution artifact file is unavailable")
    return FileResponse(path=artifact_path, media_type=artifact.mime_type, filename=artifact_path.name)


@standalone_execution_router.post(
    "/{thread_id}/cells/{cell_id}/executions/{execution_id}/cancel",
    response_model=NoteCellExecutionOut,
)
def cancel_standalone_note_cell_execution(
    thread_id: str,
    cell_id: str,
    execution_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    _get_standalone_thread(db, thread_id, tenant_id)
    execution = _get_standalone_execution(db, thread_id, cell_id, execution_id, tenant_id)
    lock_execution(db, execution)

    if execution.status in {"completed", "failed", "timed_out"}:
        raise HTTPException(status_code=409, detail=f"Cannot cancel a {execution.status} execution")
    if execution.status == "cancelled":
        return _execution_payload(execution)

    execution.cancel_requested = True
    if execution.status == "queued":
        execution.status = "cancelled"
        execution.finished_at = _now()
        event_type = "note_execution_cancelled"
    else:
        execution.status = "cancel_requested"
        event_type = "note_execution_cancel_requested"
    append_execution_event(db, execution, event_type, {"cancel_requested": True})
    db.commit()
    return _execution_payload(execution)
