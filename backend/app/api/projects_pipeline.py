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
from app.services.agent_runtime import is_edit_prompt

router = APIRouter()


def _is_non_edit_prompt(instruction: str) -> bool:
    return not is_edit_prompt(instruction)


def _ensure_agent_provider_available(project: Project) -> None:
    """Reject a repeat call after a durable non-retryable provider failure."""
    from app.services.llm import resolve_target
    from app.services.provider_guard import active_provider_block

    target_provider, _ = resolve_target("agent")
    provider = target_provider or settings.llm_provider
    block = active_provider_block(project, provider)
    if block is None:
        return
    raise HTTPException(
        status_code=409,
        detail={
            "message": block.get("message") or "The configured language-model provider is blocked.",
            "provider_failure": block,
        },
    )


def _refresh_study_manifest(db: Session, project: Project):
    """Rebuild the manifest from the current upload rows before a pipeline step."""
    from app.services.study_manifest import build_study_manifest

    files = db.query(UploadedFile).filter(UploadedFile.project_id == project.id).all()
    project.study_manifest = build_study_manifest(files)
    return files, project.study_manifest


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
    _ensure_agent_provider_available(project)

    from app.services.study_manifest import manifest_errors

    _refresh_study_manifest(db, project)
    # Roles are still intentionally unresolved at this point. Let the LLM
    # classify them before applying the role-dependent input contract.
    blocking_errors = manifest_errors(project.study_manifest, include_input_contract=False)
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

    from app.tasks.analysis import PLAN_INSTRUCTION, run_agent_job
    _dispatch_task(run_agent_job, project, job, db, background_tasks, task_kwargs={
        "instruction": PLAN_INSTRUCTION,
        "job_kind": "plan",
    })

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
    _ensure_agent_provider_available(project)

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

    from app.tasks.analysis import PLAN_INSTRUCTION, run_agent_job
    _dispatch_task(run_agent_job, project, job, db, background_tasks, task_kwargs={
        "instruction": PLAN_INSTRUCTION,
        "job_kind": "plan",
    })

    return job


@router.post("/{project_id}/approve")
def approve_plan(
    project_id: str,
    data: PlanApproval,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Update the analysis plan; retained for API compatibility."""
    project = get_project_for_tenant(db, project_id, tenant_id)

    from app.services.study_manifest import manifest_errors

    _refresh_study_manifest(db, project)
    blocking_errors = manifest_errors(
        project.study_manifest,
        include_input_contract=not getattr(settings, "project_agent_enabled", True),
    )
    if blocking_errors:
        db.commit()
        raise HTTPException(
            status_code=422,
            detail={"message": "Study inputs are not valid for this plan", "errors": blocking_errors},
        )

    project.analysis_plan = data.plan.model_dump()
    project.status = "planned"
    db.commit()

    from app.services.agent_runtime import record_agent_action, refresh_project_memory, set_agent_state
    set_agent_state(db, project, "idle", "Analysis plan updated")
    refresh_project_memory(db, project)
    record_agent_action(db, project, "plan", "updated", "Analysis plan updated")
    return {"status": "planned"}


@router.post("/{project_id}/generate", response_model=JobOut, status_code=202)
def start_generation(
    project_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Start generating the Quarto project."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    _ensure_agent_provider_available(project)
    if not project.analysis_plan:
        raise HTTPException(status_code=400, detail="No analysis plan")

    from app.services.study_manifest import manifest_errors

    _refresh_study_manifest(db, project)
    blocking_errors = manifest_errors(
        project.study_manifest,
        include_input_contract=not getattr(settings, "project_agent_enabled", True),
    )
    if blocking_errors:
        db.commit()
        raise HTTPException(
            status_code=422,
            detail={"message": "Study inputs are not valid for generation", "errors": blocking_errors},
        )

    job = Job(project_id=project_id, job_type="generate", status="pending")
    db.add(job)
    project.status = "generating"
    db.commit()
    db.refresh(job)

    from app.services.agent_runtime import record_agent_action, set_agent_state
    set_agent_state(db, project, "generating", "Building the report with the agent loop")
    record_agent_action(db, project, "generate", "started", "Building the report with the agent loop", job_id=str(job.id))

    from app.tasks.analysis import BUILD_INSTRUCTION, run_agent_job
    _dispatch_task(run_agent_job, project, job, db, background_tasks, task_kwargs={
        "instruction": BUILD_INSTRUCTION,
        "job_kind": "generate",
    })

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

    from app.tasks.analysis import run_agent_job
    _dispatch_task(run_agent_job, project, job, db, background_tasks, task_kwargs={
        "instruction": (
            "Render the report now with render_report. If the render fails, read the "
            "errors, repair the workspace source, and render again until it passes. "
            "Finish with validate_report for anything structural."
        ),
        "job_kind": "render",
    })

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

    from app.tasks.analysis import edit_instruction, run_agent_job
    if settings.task_backend.lower() == "celery":
        run_agent_job.delay(str(project.id), str(job.id), instruction=edit_instruction(data.instruction), job_kind="edit")
    elif settings.task_backend.lower() == "background":
        background_tasks.add_task(run_agent_job, str(project.id), str(job.id), instruction=edit_instruction(data.instruction), job_kind="edit")
    else:
        raise HTTPException(status_code=500, detail=f"Unsupported task backend: {settings.task_backend}")

    return job
