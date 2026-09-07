"""Project build, render, and edit pipeline endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_tenant, get_current_user_id, get_project_for_tenant
from app.config import settings
from app.database import get_db
from app.models.project import Job, Project
from app.schemas.schemas import (
    ClarificationRequest,
    ClarificationResumeOut,
    ClarificationsSubmit,
    EditRequest,
    JobOut,
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


def _ensure_no_pending_clarifications(project: Project) -> None:
    """Do not let another mutation bypass a required user decision."""
    if (project.agent_memory or {}).get("pending_clarifications"):
        raise HTTPException(
            status_code=409,
            detail="Answer every pending clarification before starting another workspace operation",
        )


def _project_instruction_or_422(project: Project) -> str:
    """Return the stored user request for compatibility pipeline routes.

    Pipeline routes are still supported for older clients, but they must not
    fabricate a request when a project has no user instruction.
    """
    from app.tasks.analysis import user_instruction_for

    instruction = user_instruction_for(project)
    if not instruction:
        raise HTTPException(status_code=422, detail="Describe the analysis before starting the report.")
    return instruction


def _ensure_no_active_jobs(db: Session, project_id: str) -> None:
    """Prevent two writers from mutating one project at the same time."""
    # Serialize the check-and-create sequence on PostgreSQL. SQLite ignores
    # row locks, but the durable run state still provides the same observation
    # semantics for local development.
    db.query(Project.id).filter(Project.id == str(project_id)).with_for_update().first()
    active = (
        db.query(Job)
        .filter(
            Job.project_id == str(project_id),
            Job.agent_run_id.is_(None),
            Job.status.in_({"pending", "running", "cancel_requested"}),
        )
        .order_by(Job.created_at.desc())
        .first()
    )
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "This project already has an active operation",
                "job_id": str(active.id),
                "agent_run_id": str(active.agent_run_id) if active.agent_run_id else None,
                "status": active.status,
            },
        )
    from app.models.runs import AgentRun

    active_run = (
        db.query(AgentRun)
        .filter(
            AgentRun.project_id == str(project_id),
            AgentRun.status.in_({"queued", "running", "waiting_tool", "cancel_requested"}),
        )
        .order_by(AgentRun.created_at.desc())
        .first()
    )
    if active_run is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "This project already has an active agent run",
                "agent_run_id": str(active_run.id),
                "status": active_run.status,
            },
        )


def _create_pipeline_job(
    db: Session,
    project: Project,
    *,
    job_type: str,
    tenant_id: str,
    user_id: str,
    instruction: str,
    existing_agent_run_id: str | None = None,
) -> tuple[Job, str]:
    """Create a compatibility Job backed by one durable AgentRun."""
    _ensure_no_active_jobs(db, str(project.id))
    from app.models.runs import AgentRun
    from app.services.agent_runs import create_or_get_agent_run, transition_agent_run

    job = Job(project_id=str(project.id), job_type=job_type, status="pending")
    db.add(job)
    db.flush()
    if existing_agent_run_id:
        run = (
            db.query(AgentRun)
            .filter(
                AgentRun.id == str(existing_agent_run_id),
                AgentRun.project_id == str(project.id),
                AgentRun.tenant_id == str(tenant_id),
            )
            .one_or_none()
        )
        if run is None:
            db.rollback()
            raise HTTPException(status_code=409, detail="The clarification run no longer exists")
        if run.status != "paused":
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail=f"The clarification run is not resumable (status={run.status})",
            )
        metadata = dict(run.run_metadata or {})
        metadata.update({
            "job_id": str(job.id),
            "job_type": str(job_type),
            "instruction": str(instruction or ""),
        })
        run.run_metadata = metadata
        transition_agent_run(
            db,
            run,
            "running",
            event_type="run_resumed",
            payload={"job_type": str(job_type), "reason": "clarification answered"},
        )
    else:
        run, _created = create_or_get_agent_run(
            db,
            tenant_id=str(tenant_id),
            owner_id=str(user_id),
            surface="workspace",
            kind=f"pipeline_{job_type}",
            idempotency_scope=f"pipeline:{project.id}:{job_type}",
            idempotency_key=str(job.id),
            request_payload={
                "project_id": str(project.id),
                "job_type": str(job_type),
                "instruction": str(instruction or ""),
            },
            project_id=str(project.id),
            run_metadata={
                "job_id": str(job.id),
                "job_type": str(job_type),
                "instruction": str(instruction or ""),
            },
        )
    job.agent_run_id = str(run.id)
    db.commit()
    db.refresh(job)
    return job, str(run.id)


def _sync_pipeline_project_state(
    db: Session,
    project: Project,
    agent_run_id: str,
    *,
    summary: str | None = None,
    details: dict | None = None,
) -> None:
    """Project a pipeline AgentRun into the Project read-model fields."""
    from app.models.runs import AgentRun
    from app.services.agent_runs import sync_project_from_agent_run

    run = db.query(AgentRun).filter(AgentRun.id == str(agent_run_id)).one_or_none()
    if run is not None:
        sync_project_from_agent_run(
            db,
            run,
            project,
            summary=summary,
            details=details,
        )


def _mark_dispatch_failed(db: Session, job: Job, detail: str) -> None:
    """Record dispatch failure on AgentRun, then project the compatibility Job."""
    if not job.agent_run_id:
        return
    from app.models.runs import AgentRun
    from app.services.agent_runs import TERMINAL_RUN_STATUSES, sync_compatibility_jobs, transition_agent_run

    run = db.query(AgentRun).filter(AgentRun.id == str(job.agent_run_id)).one_or_none()
    if run is None:
        return
    if run.status not in TERMINAL_RUN_STATUSES:
        transition_agent_run(
            db,
            run,
            "failed",
            event_type="run_failed",
            payload={"error": str(detail)[:1000], "phase": "dispatch"},
        )
        run.result_payload = {
            "message": "Agent task could not be dispatched.",
            "error": str(detail)[:4000],
            "status": "failed",
        }
    sync_compatibility_jobs(db, run, error=detail)


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
        # Allocate and persist the worker handle before publishing the task.
        # A user can press Stop immediately after the 202 response; waiting
        # for the worker to start and report its id creates an un-cancellable
        # race window.
        task_id = str(uuid.uuid4())
        if job.agent_run_id:
            from app.models.runs import AgentRun

            run = db.query(AgentRun).filter(AgentRun.id == str(job.agent_run_id)).one_or_none()
            if run is not None:
                metadata = dict(run.run_metadata or {})
                metadata["celery_task_id"] = task_id
                run.run_metadata = metadata
                db.commit()
        try:
            apply_async = getattr(task_func, "apply_async", None)
            if apply_async is not None:
                result = apply_async(
                    args=[str(project.id), str(job.id)],
                    kwargs=dispatch_kwargs,
                    task_id=task_id,
                )
            else:  # pragma: no cover - compatibility with simple test doubles
                result = task_func.delay(str(project.id), str(job.id), **dispatch_kwargs)
            actual_task_id = str(getattr(result, "id", "") or task_id).strip()
            if actual_task_id != task_id and job.agent_run_id:
                from app.models.runs import AgentRun

                run = db.query(AgentRun).filter(AgentRun.id == str(job.agent_run_id)).one_or_none()
                if run is not None:
                    metadata = dict(run.run_metadata or {})
                    metadata["celery_task_id"] = actual_task_id
                    run.run_metadata = metadata
                    db.commit()
            return
        except Exception as exc:
            detail = f"Failed to enqueue Celery task: {exc}"
            _mark_dispatch_failed(db, job, detail)
            _sync_pipeline_project_state(db, project, str(job.agent_run_id or ""), summary=detail)
            raise HTTPException(status_code=503, detail=detail) from exc

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

    detail = f"Unknown task backend: {settings.task_backend}"
    _mark_dispatch_failed(db, job, detail)
    _sync_pipeline_project_state(db, project, str(job.agent_run_id or ""), summary=detail)
    raise HTTPException(status_code=500, detail=detail)

@router.get("/{project_id}/clarifications", response_model=ClarificationRequest | None)
def get_clarifications(
    project_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Return the workspace agent's pending clarification questions, if any."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    pending = (project.agent_memory or {}).get("pending_clarifications")
    if not pending:
        return None
    return ClarificationRequest(**pending)


@router.post("/{project_id}/clarifications", response_model=JobOut | ClarificationResumeOut, status_code=202)
def submit_clarifications(
    project_id: str,
    data: ClarificationsSubmit,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
    user_id: str = Depends(get_current_user_id),
):
    """Require every answer, then resume the waiting native turn or legacy build."""
    project = get_project_for_tenant(db, project_id, tenant_id)

    agent_memory = dict(project.agent_memory or {})
    pending = agent_memory.get("pending_clarifications")
    pending_run_id = str(agent_memory.get("pending_clarifications_run_id") or "").strip() or None
    if not isinstance(pending, dict):
        raise HTTPException(status_code=409, detail="This project is not waiting for clarification")

    pending_questions = [
        item for item in pending.get("questions") or [] if isinstance(item, dict)
    ]
    question_by_id = {
        str(item.get("id") or ""): item
        for item in pending_questions
        if str(item.get("id") or "").strip()
    }
    if not question_by_id or len(question_by_id) != len(pending_questions):
        raise HTTPException(status_code=409, detail="The pending clarification request is invalid")

    answer_ids = [answer.id for answer in data.answers]
    duplicate_ids = sorted({
        answer_id
        for answer_id in answer_ids
        if answer_ids.count(answer_id) > 1
    })

    answer_by_id = {answer.id: answer for answer in data.answers}
    missing = [
        question_id
        for question_id in question_by_id
        if question_id not in answer_by_id
        or not [value for value in answer_by_id[question_id].values if value.strip()]
    ]
    unknown = sorted(set(answer_by_id) - set(question_by_id))
    if missing or unknown or duplicate_ids:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Every pending clarification must be answered exactly once",
                "missing_question_ids": missing,
                "unknown_question_ids": unknown,
                "duplicate_question_ids": duplicate_ids,
            },
        )

    normalized_answers: list[dict] = []
    for question_id, question in question_by_id.items():
        values = [value.strip() for value in answer_by_id[question_id].values if value.strip()]
        allowed = {str(option) for option in question.get("options") or []}
        if not bool(question.get("allow_custom")) and any(value not in allowed for value in values):
            raise HTTPException(
                status_code=422,
                detail=f"Question {question_id} only accepts one of its listed options",
            )
        if not bool(question.get("multiple")) and len(values) != 1:
            raise HTTPException(
                status_code=422,
                detail=f"Question {question_id} requires exactly one answer",
            )
        normalized_answers.append(
            {
                "id": question_id,
                "prompt": str(question.get("prompt") or "").strip(),
                "values": values,
            }
        )

    stored = {
        item["id"]: item
        for item in agent_memory.get("clarifications") or []
        if isinstance(item, dict)
    }
    for answer in normalized_answers:
        stored[answer["id"]] = answer
    agent_memory["clarifications"] = list(stored.values())
    if str(pending.get("runtime") or "") == "codex_app_server":
        from app.models.runs import AgentRun

        request_id = str(pending.get("request_id") or "").strip()
        candidate = (
            db.query(AgentRun)
            .filter(
                AgentRun.id == str(pending_run_id or ""),
                AgentRun.project_id == str(project.id),
                AgentRun.tenant_id == str(tenant_id),
            )
            .one_or_none()
        )
        metadata = dict(candidate.run_metadata or {}) if candidate is not None else {}
        native_request = metadata.get("codex_pending_user_input")
        if (
            candidate is None
            or candidate.status not in {"running", "waiting_tool"}
            or not isinstance(native_request, dict)
            or str(native_request.get("request_id") or "") != request_id
        ):
            raise HTTPException(
                status_code=409,
                detail="The live Codex input request is no longer available",
            )
        metadata["codex_user_input_response"] = {
            "request_id": request_id,
            "answers": {
                answer["id"]: {"answers": list(answer["values"])}
                for answer in normalized_answers
            },
        }
        candidate.run_metadata = metadata
        submitted = dict(pending)
        submitted["submitted"] = True
        agent_memory["pending_clarifications"] = submitted
        project.agent_memory = agent_memory
        project.analysis_plan = None
        db.commit()
        return ClarificationResumeOut(agent_run_id=candidate.id)

    _ensure_agent_provider_available(project)
    agent_memory.pop("pending_clarifications", None)
    agent_memory.pop("pending_clarifications_run_id", None)
    project.agent_memory = agent_memory
    project.analysis_plan = None
    db.commit()

    from app.models.runs import AgentRun
    instruction = _project_instruction_or_422(project)

    resume_run_id = None
    if pending_run_id:
        candidate = (
            db.query(AgentRun)
            .filter(
                AgentRun.id == pending_run_id,
                AgentRun.project_id == str(project.id),
                AgentRun.tenant_id == str(tenant_id),
            )
            .one_or_none()
        )
        if candidate is not None and candidate.status == "paused":
            resume_run_id = str(candidate.id)

    job, agent_run_id = _create_pipeline_job(
        db,
        project,
        job_type="generate",
        tenant_id=tenant_id,
        user_id=user_id,
        instruction=instruction,
        existing_agent_run_id=resume_run_id,
    )
    _sync_pipeline_project_state(db, project, agent_run_id)

    from app.services.agent_runtime import record_agent_action
    record_agent_action(db, project, "generate", "restarted", "OpenCode continuing after clarifications", job_id=str(job.id))

    from app.tasks.analysis import run_agent_job
    _dispatch_task(run_agent_job, project, job, db, background_tasks, task_kwargs={
        "instruction": instruction,
        "job_kind": "generate",
        "agent_run_id": agent_run_id,
    })

    return job


@router.post("/{project_id}/generate", response_model=JobOut, status_code=202)
def start_generation(
    project_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
    user_id: str = Depends(get_current_user_id),
):
    """Start generating the Quarto project with OpenCode."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    _ensure_no_pending_clarifications(project)
    _ensure_agent_provider_available(project)

    instruction = _project_instruction_or_422(project)

    job, agent_run_id = _create_pipeline_job(
        db,
        project,
        job_type="generate",
        tenant_id=tenant_id,
        user_id=user_id,
        instruction=instruction,
    )
    _sync_pipeline_project_state(db, project, agent_run_id)

    from app.services.agent_runtime import record_agent_action
    record_agent_action(db, project, "generate", "started", "OpenCode workspace build", job_id=str(job.id))

    from app.tasks.analysis import run_agent_job
    _dispatch_task(run_agent_job, project, job, db, background_tasks, task_kwargs={
        "instruction": instruction,
        "job_kind": "generate",
        "agent_run_id": agent_run_id,
    })

    return job


@router.post("/{project_id}/run", response_model=JobOut, status_code=202)
def start_rendering(
    project_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
    user_id: str = Depends(get_current_user_id),
):
    """Start rendering the generated project."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    _ensure_no_pending_clarifications(project)
    if not project.project_dir:
        raise HTTPException(status_code=400, detail="No generated project")

    job, agent_run_id = _create_pipeline_job(
        db,
        project,
        job_type="render",
        tenant_id=tenant_id,
        user_id=user_id,
        instruction="Render or rebuild the Quarto report and repair any errors.",
    )
    _sync_pipeline_project_state(db, project, agent_run_id)

    from app.services.agent_runtime import record_agent_action
    record_agent_action(db, project, "render", "started", "Rendering report", job_id=str(job.id))

    from app.tasks.analysis import run_agent_job
    _dispatch_task(run_agent_job, project, job, db, background_tasks, task_kwargs={
        "instruction": (
            "Render or rebuild the Quarto report with bash. If it fails, read "
            "the errors, repair the source, and render again until it passes."
        ),
        "job_kind": "render",
        "agent_run_id": agent_run_id,
    })

    return job


@router.post("/{project_id}/edit", response_model=JobOut, status_code=202)
def edit_project(
    project_id: str,
    data: EditRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
    user_id: str = Depends(get_current_user_id),
):
    """Start an OmicsBase edit pass on the project source code."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    _ensure_no_pending_clarifications(project)
    if not project.project_dir:
        raise HTTPException(status_code=400, detail="No generated project directory available to edit")
    if _is_non_edit_prompt(data.instruction):
        raise HTTPException(
            status_code=400,
            detail="This prompt does not describe a code or report edit. Ask for a concrete change, or use the status panel to inspect progress.",
        )

    job, agent_run_id = _create_pipeline_job(
        db,
        project,
        job_type="edit",
        tenant_id=tenant_id,
        user_id=user_id,
        instruction=data.instruction,
    )
    _sync_pipeline_project_state(db, project, agent_run_id, details={"instruction": data.instruction})

    from app.services.agent_runtime import record_agent_action
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
    _dispatch_task(
        run_agent_job,
        project,
        job,
        db,
        background_tasks,
        task_kwargs={
            "instruction": edit_instruction(data.instruction),
            "job_kind": "edit",
            "agent_run_id": agent_run_id,
        },
    )

    return job
