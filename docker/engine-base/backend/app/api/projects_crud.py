"""Project CRUD API endpoints."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_tenant, get_current_user_id, get_project_for_tenant
from app.config import settings
from app.database import get_db
from app.models.project import Project, UploadedFile
from app.schemas.schemas import ProjectCreate, ProjectOut, ProjectUpdate
from app.services.project_workspace import ensure_project_workspace

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/", response_model=list[ProjectOut])
def list_projects(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """List all projects for the authenticated tenant."""
    projects = (
        db.query(Project)
        .filter(Project.tenant_id == tenant_id)
        .order_by(Project.created_at.desc())
        .all()
    )
    return projects


@router.post("/", response_model=ProjectOut, status_code=201)
async def create_project(
    data: ProjectCreate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
    user_id: str = Depends(get_current_user_id),
):
    """Create a new project scoped to the current tenant and user."""
    explicit_name = (data.name or "").strip()
    project_name = explicit_name or "New project"

    project = Project(
        name=project_name,
        name_source="user" if explicit_name else "default",
        question=data.question,
        notes=data.notes,
        custom_plan_text=data.custom_plan_text,
        auto_build=data.auto_build,
        owner_id=user_id,
        tenant_id=tenant_id,
        agent_state="idle",
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    ensure_project_workspace(project)
    db.commit()
    db.refresh(project)
    return project


@router.post("/{project_id}/title", response_model=ProjectOut)
async def generate_project_name(
    project_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Generate a one-time project title from its durable request and inputs."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    if project.name_source != "default":
        return project

    from app.services.titles import claim_project_auto_title, generate_title, title_context

    files = db.query(UploadedFile).filter(UploadedFile.project_id == str(project.id)).all()
    intent = (project.question or project.custom_plan_text or project.notes or "").strip()
    if not intent and files:
        intent = "Analyze the uploaded study data"
    if not intent:
        return project

    expected_name = str(project.name)
    try:
        proposed_name = await generate_title(
            kind="project",
            user_intent=intent,
            context=title_context(
                project.custom_plan_text,
                project.notes,
                file_names=[str(item.original_name or "") for item in files],
            ),
        )
        claim_project_auto_title(
            db,
            project_id=str(project.id),
            expected_name=expected_name,
            proposed_name=proposed_name,
        )
    except Exception as exc:
        logger.warning("Project auto-titling failed for %s: %s", project.id, exc)

    db.expire_all()
    return get_project_for_tenant(db, project_id, tenant_id)


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Get a project by ID ensuring tenant ownership."""
    return get_project_for_tenant(db, project_id, tenant_id)


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: str,
    data: ProjectUpdate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Update a project ensuring tenant ownership."""
    project = get_project_for_tenant(db, project_id, tenant_id)

    update_data = data.model_dump(exclude_unset=True)
    if "name" in update_data:
        requested_name = update_data["name"]
        if requested_name is None or not requested_name.strip():
            raise HTTPException(status_code=422, detail="Project name must not be blank")
        update_data["name"] = requested_name.strip()

    for key, value in update_data.items():
        setattr(project, key, value)

    if "name" in update_data:
        project.name_source = "user"

    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=204)
def delete_project(
    project_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Delete a project and its files ensuring tenant ownership."""
    project = get_project_for_tenant(db, project_id, tenant_id)

    if project.project_dir:
        project_path = Path(project.project_dir)
        if project_path.exists():
            shutil.rmtree(project_path)
    upload_path = Path(settings.projects_dir) / "uploads" / str(project_id)
    if upload_path.exists():
        shutil.rmtree(upload_path)

    db.delete(project)
    db.commit()
