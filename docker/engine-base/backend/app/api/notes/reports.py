"""Project-scoped report listing and NoteThread export routes."""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.services.note_store import get_project_thread as _get_thread, now as _now
from app.auth import get_current_tenant, get_project_for_tenant
from app.database import get_db
from app.models.notes import Report
from app.schemas.schemas import NoteThreadReportExportRequest, ReportOut
from app.services.note_report import build_note_qmd, report_payload, safe_report_slug

router = APIRouter()

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
