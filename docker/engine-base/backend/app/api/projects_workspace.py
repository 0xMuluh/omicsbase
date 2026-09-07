"""Launch configuration for native VS Code project workspaces."""

from __future__ import annotations

import json
from urllib.parse import urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_tenant, get_project_for_tenant
from app.config import settings
from app.database import get_db
from app.models.project import UploadedFile
from app.services.project_workspace import PROJECT_BRIEF_NAME, ensure_project_workspace

router = APIRouter()


class ProjectWorkspaceOut(BaseModel):
    project_id: str
    project_name: str
    folder: str
    url: str


def _remote_uri(base_url: str, path: str) -> str:
    authority = urlsplit(base_url).netloc
    return f"vscode-remote://{authority}{path}"


def _workspace_url(folder: str) -> str:
    base_url = settings.vscode_web_url.rstrip("/") + "/"
    payload: list[list[str]] = [
        ["openFile", _remote_uri(base_url, f"{folder}/{PROJECT_BRIEF_NAME}")],
    ]
    parsed = urlsplit(base_url)
    query = urlencode(
        {
            "folder": folder,
            "payload": json.dumps(payload, separators=(",", ":")),
        }
    )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))


@router.get("/{project_id}/workspace", response_model=ProjectWorkspaceOut)
def get_project_workspace(
    project_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Resolve one authenticated project to its native VS Code workspace."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    uploaded_files = (
        db.query(UploadedFile)
        .filter(UploadedFile.project_id == project_id)
        .all()
    )
    workspace = ensure_project_workspace(project, uploaded_files=uploaded_files)
    db.commit()

    folder = str(workspace)
    return ProjectWorkspaceOut(
        project_id=str(project.id),
        project_name=project.name,
        folder=folder,
        url=_workspace_url(folder),
    )
