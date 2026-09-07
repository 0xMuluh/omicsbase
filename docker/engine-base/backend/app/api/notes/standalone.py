"""Standalone Notes routes for tenant-scoped notebook threads.

Project-scoped Notes routes live in the sibling modules under app.api.notes;
this module owns only the standalone entry point and NoteThread transfer flow.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import get_current_tenant, get_current_user_id, get_project_for_tenant
from app.config import settings
from app.database import get_db
from app.models.notes import NoteCell, NoteCellRevision, NoteThread
from app.models.project import Project, UploadedFile
from app.schemas.schemas import (
    DatasetImportRequest,
    NoteCellCreate,
    NoteCellOut,
    NoteCellRevisionCreate,
    NoteCellRevisionOut,
    NoteThreadAttach,
    NoteThreadCreate,
    NoteThreadOut,
    NoteThreadSummaryOut,
    NoteThreadUpdate,
    NoteThreadWorkspaceCreate,
)
from app.services.agent_runtime import normalise_cell_type
from app.services.note_data import MAX_THREAD_UPLOAD_BYTES
from app.services.note_payloads import (
    cell_payload as _cell_payload,
    revision_payload as _revision_payload,
)
from app.services.note_scope import standalone_storage_path
from app.services.note_store import (
    delete_note_thread_files as _delete_note_thread_files,
    get_cell as _get_cell,
    get_standalone_thread as _get_standalone_thread,
    now as _now,
    publish_note_event as _publish_note_event,
    thread_payload as _thread_payload,
    thread_summary_payload as _thread_summary_payload,
)

# Standalone Chat/Notes entry point. A thread can later be attached to a
# full Project workspace without changing its immutable cells or executions.
standalone_router = APIRouter(prefix="/api/notes", tags=["notes"])


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
    title = data.title.strip()
    thread = NoteThread(
        id=thread_id,
        project_id=None,
        tenant_id=tenant_id,
        owner_id=user_id,
        storage_path=str(standalone_storage_path(thread_id)),
        title=title,
        title_source="default" if title.lower() == "untitled note" else "user",
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
        thread.title_source = "user"
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
                summary = {"name": source.name, "size_bytes": data_destination.stat().st_size, "inspection_status": "deferred"}
                imported_records.append(
                    UploadedFile(
                        project_id=str(project.id),
                        file_role="other",
                        original_name=source.name,
                        detected_format="uninspected",
                        file_summary=summary,
                        file_path=str(data_destination),
                    )
                )
        if imported_records:
            db.add_all(imported_records)
            db.flush()

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
    agent_run_id = None
    if data.auto_build:
        if not project.files:
            auto_build_reason = "Auto-build was requested, but the NoteThread has no uploaded study data."
        else:
            try:
                from app.api.projects_pipeline import _create_pipeline_job, _dispatch_task, _sync_pipeline_project_state
                from app.services.agent_runtime import record_agent_action
                from app.tasks.analysis import run_agent_job, user_instruction_for

                instruction = user_instruction_for(project)
                if not instruction:
                    raise ValueError("No analysis request was carried into the workspace")

                auto_build_job, agent_run_id = _create_pipeline_job(
                    db,
                    project,
                    job_type="generate",
                    tenant_id=tenant_id,
                    user_id=user_id,
                    instruction=instruction,
                )
                _sync_pipeline_project_state(db, project, agent_run_id)
                record_agent_action(
                    db,
                    project,
                    "generate",
                    "started",
                    "OpenCode workspace build from transferred NoteThread inputs",
                    {"note_thread_id": str(thread.id), "auto_build": True},
                    job_id=str(auto_build_job.id),
                )
                _dispatch_task(
                    run_agent_job,
                    project,
                    auto_build_job,
                    db,
                    background_tasks,
                    task_kwargs={"instruction": instruction, "job_kind": "generate", "agent_run_id": agent_run_id},
                )
            except Exception as exc:
                auto_build_reason = f"Auto-build could not be queued: {str(exc)[:500]}"
                if auto_build_job is not None:
                    auto_build_job.status = "failed"
                    auto_build_job.error = auto_build_reason
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
