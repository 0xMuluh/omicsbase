"""Project planning, generation, and execution pipeline endpoints."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_tenant, get_project_for_tenant
from app.config import settings
from app.database import get_db
from app.models.project import Job, Project, UploadedFile
from app.schemas.schemas import (
    ClarificationRequest,
    ClarificationsSubmit,
    EditRequest,
    JobOut,
    PlanApproval,
)
from app.services.assistant import is_edit_prompt

router = APIRouter()


def _is_non_edit_prompt(instruction: str) -> bool:
    return not is_edit_prompt(instruction)


def _dispatch_task(
    task_func,
    project: Project,
    job: Job,
    db: Session,
    background_tasks: BackgroundTasks | None = None,
    *,
    task_kwargs: dict | None = None,
):
    """Dispatch a long-running analysis task through the configured backend."""
    task_backend = settings.task_backend.lower()
    dispatch_kwargs = dict(task_kwargs or {})

    if task_backend == "celery":
        try:
            task_func.delay(str(project.id), str(job.id), **dispatch_kwargs)
            return
        except Exception as exc:
            job.status = "failed"
            job.error = f"Failed to enqueue Celery task: {exc}"
            project.status = "failed"
            db.commit()
            raise HTTPException(status_code=503, detail=job.error) from exc

    if task_backend == "background":
        if background_tasks is None:
            raise HTTPException(status_code=500, detail="Background task dispatcher unavailable")
        background_tasks.add_task(
            task_func,
            str(project.id),
            str(job.id),
            **dispatch_kwargs,
        )
        return

    job.status = "failed"
    job.error = f"Unknown task backend: {settings.task_backend}"
    project.status = "failed"
    db.commit()
    raise HTTPException(status_code=500, detail=job.error)


@router.post("/{project_id}/plan", response_model=JobOut, status_code=202)
def start_planning(
    project_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Start the planning process."""
    project = get_project_for_tenant(db, project_id, tenant_id)

    from app.services.study_manifest import build_study_manifest, manifest_errors

    files = db.query(UploadedFile).filter(UploadedFile.project_id == project_id).all()
    project.study_manifest = build_study_manifest(files)
    blocking_errors = manifest_errors(project.study_manifest)
    if blocking_errors:
        db.commit()
        raise HTTPException(
            status_code=422,
            detail={"message": "Study inputs are not ready", "errors": blocking_errors},
        )

    job = Job(project_id=project_id, job_type="plan", status="pending")
    db.add(job)
    project.status = "planning"
    db.commit()
    db.refresh(job)

    from app.services.agent_runtime import record_agent_action, set_agent_state
    set_agent_state(db, project, "planning", "Planning analysis workflow")
    record_agent_action(db, project, "plan", "started", "Planning analysis workflow", job_id=str(job.id))

    from app.tasks.analysis import run_planning
    _dispatch_task(run_planning, project, job, db, background_tasks)

    return job


@router.get("/{project_id}/clarifications", response_model=ClarificationRequest | None)
def get_clarifications(
    project_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Return the planner's pending clarification questions, if any."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    pending = (project.agent_memory or {}).get("pending_clarifications")
    if not pending:
        return None
    return ClarificationRequest(**pending)


@router.post("/{project_id}/clarifications", response_model=JobOut, status_code=202)
def submit_clarifications(
    project_id: str,
    data: ClarificationsSubmit,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Record the user's answers and re-run planning with them."""
    project = get_project_for_tenant(db, project_id, tenant_id)

    agent_memory = dict(project.agent_memory or {})
    stored = {
        item["id"]: item
        for item in agent_memory.get("clarifications") or []
        if isinstance(item, dict)
    }
    for answer in data.answers:
        stored[answer.id] = answer.model_dump()
    agent_memory["clarifications"] = list(stored.values())
    agent_memory.pop("pending_clarifications", None)
    project.agent_memory = agent_memory
    project.status = "planning"
    project.analysis_plan = None
    db.commit()

    job = Job(project_id=project_id, job_type="plan", status="pending")
    db.add(job)
    project.status = "planning"
    db.commit()
    db.refresh(job)

    from app.services.agent_runtime import record_agent_action, set_agent_state
    set_agent_state(db, project, "planning", "Re-planning with answered questions")
    record_agent_action(db, project, "plan", "restarted", "Re-planning with answered questions", job_id=str(job.id))

    from app.tasks.analysis import run_planning
    _dispatch_task(run_planning, project, job, db, background_tasks)

    return job


@router.post("/{project_id}/approve")
def approve_plan(
    project_id: str,
    data: PlanApproval,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Approve (and optionally modify) the analysis plan."""
    project = get_project_for_tenant(db, project_id, tenant_id)

    project.analysis_plan = data.plan.model_dump()
    project.status = "approved"
    db.commit()

    from app.services.agent_runtime import record_agent_action, refresh_project_memory, set_agent_state
    set_agent_state(db, project, "idle", "Analysis plan approved")
    refresh_project_memory(db, project)
    record_agent_action(db, project, "plan", "approved", "User approved analysis plan")
    return {"status": "approved"}


@router.post("/{project_id}/generate", response_model=JobOut, status_code=202)
def start_generation(
    project_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Start generating the Quarto project."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    if not project.analysis_plan:
        raise HTTPException(status_code=400, detail="No approved plan")
    retrying_failed_generation = False
    if project.status == "failed":
        latest_failed_job = (
            db.query(Job)
            .filter(Job.project_id == project_id, Job.status == "failed")
            .order_by(Job.created_at.desc())
            .first()
        )
        retrying_failed_generation = bool(
            latest_failed_job and latest_failed_job.job_type == "generate"
        )
    if project.status != "approved" and not retrying_failed_generation:
        raise HTTPException(
            status_code=409,
            detail=(
                "Generation can start after plan approval or retry the latest failed generation; "
                "retry the failed pipeline stage instead."
            ),
        )

    job = Job(project_id=project_id, job_type="generate", status="pending")
    db.add(job)
    project.status = "generating"
    db.commit()
    db.refresh(job)

    from app.services.agent_runtime import record_agent_action, set_agent_state
    set_agent_state(db, project, "generating", "Generating source project")
    record_agent_action(db, project, "generate", "started", "Generating source project", job_id=str(job.id))

    from app.tasks.analysis import run_generation
    _dispatch_task(run_generation, project, job, db, background_tasks)

    return job


@router.post("/{project_id}/run", response_model=JobOut, status_code=202)
def start_rendering(
    project_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Start rendering the generated project."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    if not project.project_dir:
        raise HTTPException(status_code=400, detail="No generated project")

    job = Job(project_id=project_id, job_type="render", status="pending")
    db.add(job)
    project.status = "rendering"
    db.commit()
    db.refresh(job)

    from app.services.agent_runtime import record_agent_action, set_agent_state
    set_agent_state(db, project, "rendering", "Rendering report")
    record_agent_action(db, project, "render", "started", "Rendering report", job_id=str(job.id))

    from app.tasks.analysis import run_rendering
    _dispatch_task(run_rendering, project, job, db, background_tasks)

    return job


@router.post("/{project_id}/edit", response_model=JobOut, status_code=202)
def edit_project(
    project_id: str,
    data: EditRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Start an OmicsBase edit pass on the project source code."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    if not project.project_dir:
        raise HTTPException(status_code=400, detail="No generated project directory available to edit")
    if _is_non_edit_prompt(data.instruction):
        raise HTTPException(
            status_code=400,
            detail="This prompt does not describe a code or report edit. Ask for a concrete change, or use the status panel to inspect progress.",
        )

    job = Job(project_id=project_id, job_type="edit", status="pending")
    db.add(job)
    project.status = "rendering"
    db.commit()
    db.refresh(job)

    from app.services.agent_runtime import record_agent_action, set_agent_state
    set_agent_state(db, project, "editing", "Editing generated source", {"instruction": data.instruction})
    record_agent_action(
        db,
        project,
        "edit",
        "started",
        "Editing generated source",
        {"instruction": data.instruction},
        job_id=str(job.id),
    )

    from app.tasks.analysis import run_editing
    if settings.task_backend.lower() == "celery":
        run_editing.delay(str(project.id), str(job.id), instruction=data.instruction)
    elif settings.task_backend.lower() == "background":
        background_tasks.add_task(run_editing, str(project.id), str(job.id), instruction=data.instruction)
    else:
        raise HTTPException(status_code=500, detail=f"Unsupported task backend: {settings.task_backend}")

    return job
