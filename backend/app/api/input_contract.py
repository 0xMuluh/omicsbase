"""Validated, read-only study input contract endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_tenant, get_project_for_tenant
from app.database import get_db
from app.models.project import UploadedFile
from app.services.omics_contract import build_input_contract

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("/{project_id}/input-contract")
def get_input_contract(
    project_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Return deterministic input/identifier/orientation validation for a project."""
    get_project_for_tenant(db, project_id, tenant_id)
    files = (
        db.query(UploadedFile)
        .filter(UploadedFile.project_id == project_id)
        .order_by(UploadedFile.created_at.asc())
        .all()
    )
    return build_input_contract(files)

