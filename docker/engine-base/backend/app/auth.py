"""Authentication and Multi-Tenant Isolation Dependencies."""

from __future__ import annotations

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings


def get_current_tenant(request: Request) -> str:
    """Extract tenant ID from request header X-Tenant-ID.

    In dev_mode, defaults to 'default_tenant' when header is absent.
    In production, raises 401 if the header is missing.
    """
    tenant_id = request.headers.get("x-tenant-id") or request.headers.get("X-Tenant-ID")
    if tenant_id and tenant_id.strip():
        return tenant_id.strip()
    if settings.dev_mode:
        return "default_tenant"
    raise HTTPException(status_code=401, detail="Missing X-Tenant-ID header")


def get_current_user_id(request: Request) -> str:
    """Extract user ID from request header X-User-ID.

    In dev_mode, defaults to 'default_user' when header is absent.
    In production, raises 401 if the header is missing.
    """
    user_id = request.headers.get("x-user-id") or request.headers.get("X-User-ID")
    if user_id and user_id.strip():
        return user_id.strip()
    if settings.dev_mode:
        return "default_user"
    raise HTTPException(status_code=401, detail="Missing X-User-ID header")


def get_project_for_tenant(db: Session, project_id: str, tenant_id: str) -> "Project":
    """Fetch project ensuring tenant ownership matching tenant_id. Raises 404 if not found or unauthorized."""
    from app.models.project import Project

    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.tenant_id == tenant_id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project

