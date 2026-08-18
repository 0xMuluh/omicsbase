"""Project file management and lock endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.auth import get_current_tenant, get_project_for_tenant
from app.config import settings
from app.database import get_db
from app.models.project import UploadedFile
from app.schemas.schemas import FileLocksUpdate, UploadedFileOut
from app.services.file_inspector import inspect_file

router = APIRouter()


@router.post("/{project_id}/files", response_model=UploadedFileOut, status_code=201)
async def upload_file(
    project_id: str,
    file_role: str = "auto",
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Upload a file to a project ensuring tenant ownership."""
    project = get_project_for_tenant(db, project_id, tenant_id)

    upload_dir = Path(settings.projects_dir) / "uploads" / str(project_id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    safe_filename = Path(file.filename or "upload").name
    file_path = upload_dir / safe_filename
    with open(file_path, "wb") as f:
        content = await file.read()
        f.write(content)

    file_summary = inspect_file(str(file_path))

    # Roles are agent-assigned during planning; "auto" has no heuristic.
    if file_role == "auto":
        file_role = "other"

    detected_format = file_summary.get("format", "unknown")
    if detected_format == "error":
        file_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=422,
            detail=f"Could not inspect {safe_filename}: {file_summary.get('error', 'unknown error')}",
        )

    uploaded = UploadedFile(
        project_id=project_id,
        file_role=file_role,
        original_name=safe_filename,
        detected_format=detected_format,
        file_summary=file_summary,
        file_path=str(file_path),
    )
    db.add(uploaded)
    db.commit()
    db.refresh(uploaded)

    from app.services.study_manifest import build_study_manifest

    project_files = db.query(UploadedFile).filter(UploadedFile.project_id == project_id).all()
    project.study_manifest = build_study_manifest(project_files)
    db.commit()
    return uploaded


@router.get("/{project_id}/files", response_model=list[UploadedFileOut])
def list_files(
    project_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """List uploaded files for a project."""
    get_project_for_tenant(db, project_id, tenant_id)
    return db.query(UploadedFile).filter(UploadedFile.project_id == project_id).all()


@router.get("/{project_id}/note-results")
def list_note_results(
    project_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Result tables from the workspace and from project-attached note executions."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    from app.services.workspace_agent import list_project_result_artifacts

    return [
        {
            "path": path,
            "name": Path(path).name,
            "source": "note" if "note-executions" in path else "workspace",
        }
        for path in list_project_result_artifacts(project)
    ]


@router.get("/{project_id}/locks")
def get_project_locks(
    project_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Return locked source paths that the agent may not edit."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    if not project.project_dir:
        return {"paths": []}
    from app.services.apply_edits import load_locks

    return {"paths": sorted(load_locks(project.project_dir))}


@router.put("/{project_id}/locks")
def update_project_locks(
    project_id: str,
    data: FileLocksUpdate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Replace the project's locked source paths."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    if not project.project_dir:
        raise HTTPException(status_code=400, detail="Project has no generated workspace yet")
    from app.services.apply_edits import save_locks

    paths = save_locks(project.project_dir, data.paths)
    return {"paths": paths}
