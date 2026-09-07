"""Example R package datasets: discovery and controlled import into studies."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_tenant, get_project_for_tenant
from app.database import get_db
from app.schemas.schemas import DatasetImportRequest

router = APIRouter(prefix="/api/datasets", tags=["datasets"])


@router.get("/importable")
def list_importable_datasets():
    """List the R package datasets that can be imported into a study."""
    from app.services.data_acquisition import list_importable_datasets

    return {"datasets": list_importable_datasets()}


@router.post("/projects/{project_id}/import")
def import_dataset_into_project(
    project_id: str,
    data: DatasetImportRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Export a known R package dataset into a study's uploads."""
    from app.services.data_acquisition import import_package_dataset

    project = get_project_for_tenant(db, project_id, tenant_id)
    try:
        result = import_package_dataset(
            db,
            project,
            package=data.package,
            dataset=data.dataset,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result.get("status") == "error":
        raise HTTPException(status_code=422, detail=result.get("error", "Dataset import failed"))
    return result
