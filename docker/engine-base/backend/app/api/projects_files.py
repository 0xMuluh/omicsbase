"""Project file management and lock endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.auth import get_current_tenant, get_project_for_tenant
from app.database import get_db
from app.models.project import UploadedFile
from app.schemas.schemas import FileLocksUpdate, UploadedFileOut
from app.services.project_workspace import ensure_project_workspace

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

    upload_dir = ensure_project_workspace(project) / "data"
    upload_dir.mkdir(parents=True, exist_ok=True)

    safe_filename = Path(file.filename or "upload").name
    file_path = upload_dir / safe_filename
    with open(file_path, "wb") as f:
        content = await file.read()
        f.write(content)

    # Upload is storage-only. The coding agent owns semantic inspection via its
    # workspace tools; do not run a hidden, deterministic pre-inspector here.
    file_summary = {
        "name": safe_filename,
        "size_bytes": len(content),
        "inspection_status": "deferred",
    }

    # Roles are agent-assigned during planning; "auto" has no heuristic.
    if file_role == "auto":
        file_role = "other"

    detected_format = "uninspected"

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
    from app.services.project_artifacts import list_project_result_artifacts

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
