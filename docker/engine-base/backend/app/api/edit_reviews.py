"""Tenant-scoped prepare/diff/approve edit review endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_tenant, get_project_for_tenant
from app.database import get_db
from app.services.edit_engine import EditBusy, EditConflict, EditEngineError, EditOperation
from app.services.edit_review import (
    approve_edit_review,
    list_edit_reviews,
    prepare_edit_review,
    read_edit_review,
    reject_edit_review,
)

router = APIRouter(prefix="/api/projects/{project_id}/edit-reviews", tags=["edit reviews"])


def _root(project) -> str:
    if not project.project_dir:
        raise HTTPException(status_code=404, detail="Project workspace is not generated")
    return project.project_dir


def _operations(payload: Any) -> list[EditOperation | dict[str, Any]]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Edit review body must be an object")
    values = payload.get("operations")
    if not isinstance(values, list) or not values:
        # Accept the same single-operation shape as edit_project for clients
        # that do not need a batch wrapper.
        values = [payload]
    return values


@router.get("")
def list_reviews(
    project_id: str,
    limit: int = 50,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    project = get_project_for_tenant(db, project_id, tenant_id)
    return {"reviews": list_edit_reviews(_root(project), limit=limit)}


@router.post("")
def prepare_review(
    project_id: str,
    body: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    project = get_project_for_tenant(db, project_id, tenant_id)
    try:
        return prepare_edit_review(
            _root(project),
            _operations(body),
            origin=str(body.get("origin") or "review"),
            summary=str(body.get("summary") or "Edit proposal"),
        )
    except EditEngineError as exc:
        raise HTTPException(status_code=409 if exc.code == "edit_conflict" else 400, detail=exc.to_dict()) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{review_id}")
def get_review(
    project_id: str,
    review_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    project = get_project_for_tenant(db, project_id, tenant_id)
    try:
        return read_edit_review(_root(project), review_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Edit review was not found") from exc


@router.post("/{review_id}/approve")
def approve_review(
    project_id: str,
    review_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    project = get_project_for_tenant(db, project_id, tenant_id)
    try:
        result = approve_edit_review(_root(project), review_id, lock_timeout=0)
    except EditBusy as exc:
        raise HTTPException(status_code=423, detail=exc.to_dict()) from exc
    except EditConflict as exc:
        raise HTTPException(status_code=409, detail=exc.to_dict()) from exc
    except EditEngineError as exc:
        raise HTTPException(status_code=400, detail=exc.to_dict()) from exc
    from app.services.agent_runtime import refresh_project_memory
    from app.services.project_edit_index import record_project_edit
    record_project_edit(db, project, result)
    refresh_project_memory(db, project)
    return result.to_dict()


@router.post("/{review_id}/reject")
def reject_review(
    project_id: str,
    review_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    project = get_project_for_tenant(db, project_id, tenant_id)
    try:
        return reject_edit_review(_root(project), review_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Edit review was not found") from exc
    except EditEngineError as exc:
        raise HTTPException(status_code=400, detail=exc.to_dict()) from exc


__all__ = ["router"]
