"""Tenant-scoped ReportPack execution provenance endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_tenant, get_project_for_tenant
from app.database import get_db
from app.services.execution_provenance import (
    list_execution_provenance,
    read_execution_provenance,
)

router = APIRouter(prefix="/api/projects/{project_id}/execution-runs", tags=["execution provenance"])


def _project_root(project) -> str:
    if not project.project_dir:
        raise HTTPException(status_code=404, detail="Project workspace is not generated")
    return project.project_dir


@router.get("")
def list_execution_runs(
    project_id: str,
    limit: int = 50,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    project = get_project_for_tenant(db, project_id, tenant_id)
    return {"runs": list_execution_provenance(_project_root(project), limit=limit)}


@router.get("/{run_id}")
def get_execution_run(
    project_id: str,
    run_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    project = get_project_for_tenant(db, project_id, tenant_id)
    try:
        return read_execution_provenance(_project_root(project), run_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Execution provenance was not found") from exc


__all__ = ["router"]
