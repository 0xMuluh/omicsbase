"""Project CRUD API endpoints."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_tenant, get_current_user_id, get_project_for_tenant
from app.config import settings
from app.database import get_db
from app.models.project import Job, Project, UploadedFile
from app.schemas.schemas import ProjectCreate, ProjectOut, ProjectUpdate

router = APIRouter()


def _ensure_agent_memory(db: Session, projects: list[Project]) -> None:
    """Lazily backfill compact agent memory for older projects if missing."""
    missing = [project for project in projects if not project.agent_memory]
    if not missing:
        return

    from app.services.agent_runtime import record_agent_action, refresh_project_memory, set_agent_state

    for project in missing:
        files = db.query(UploadedFile).filter(UploadedFile.project_id == project.id).all()
        jobs = db.query(Job).filter(Job.project_id == project.id).order_by(Job.created_at.desc()).all()
        refresh_project_memory(db, project, files=files, jobs=jobs)
        state = "needs_user" if project.status == "planned" else (project.status if project.status in {"planning", "generating", "rendering", "completed", "failed"} else "idle")
        set_agent_state(db, project, state, "Imported existing project state")
        if not project.agent_actions:
            record_agent_action(db, project, "memory", "completed", "Imported existing project state")


def _ensure_study_manifests(db: Session, projects: list[Project]) -> None:
    """Build in-memory study contracts for projects lacking them."""
    from app.services.study_manifest import build_study_manifest

    for project in projects:
        if not project.study_manifest:
            files = db.query(UploadedFile).filter(UploadedFile.project_id == project.id).all()
            project.study_manifest = build_study_manifest(files)


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
    _ensure_study_manifests(db, projects)
    _ensure_agent_memory(db, projects)
    return projects


@router.post("/", response_model=ProjectOut, status_code=201)
async def create_project(
    data: ProjectCreate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
    user_id: str = Depends(get_current_user_id),
):
    """Create a new project scoped to the current tenant and user."""
    from app.services.study_manifest import build_study_manifest

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
        study_manifest=build_study_manifest([]),
        agent_state="idle",
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    from app.services.agent_runtime import record_agent_action, refresh_project_memory
    refresh_project_memory(db, project)
    record_agent_action(db, project, "project", "completed", "Project created")
    return project


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Get a project by ID ensuring tenant ownership."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    _ensure_study_manifests(db, [project])
    _ensure_agent_memory(db, [project])
    return project


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
        memory = dict(project.agent_memory or {})
        if memory:
            project_summary = dict(memory.get("project") or {})
            project_summary.update({"name": project.name, "name_source": project.name_source})
            memory["project"] = project_summary
            project.agent_memory = memory

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
