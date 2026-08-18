"""Async analysis tasks with optional Celery support."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# Optional Celery integration
try:
    from celery import Celery
    celery_app = Celery(
        "omicsbase",
        broker=settings.redis_url,
        backend=settings.redis_url,
    )
    celery_app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        include=["app.tasks.notes"],
    )
    task_decorator = celery_app.task(bind=True)
    celery_app.conf.beat_schedule = {
        "sync-bioc-knowledge-weekly": {
            "task": "app.tasks.analysis.sync_bioc_knowledge",
            "schedule": float(settings.bioc_knowledge_sync_interval_hours) * 3600,
        }
    }
except ImportError:
    celery_app = None

    def task_decorator(func):
        """Fallback decorator when Celery is not installed."""
        def delay_func(*args, **kwargs):
            raise ImportError("Celery is not installed; falling back to FastAPI BackgroundTasks")
        func.delay = delay_func
        return func


ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "created": {"planning", "planned", "failed"},
    "planning": {"planned", "needs_user", "needs_clarification", "failed"},
    "needs_clarification": {"planning", "failed"},
    "planned": {"approved", "planning", "failed"},
    "approved": {"generating", "failed"},
    "generating": {"rendering", "repairing", "failed"},
    "rendering": {"repairing", "reviewing", "completed", "failed"},
    "repairing": {"rendering", "failed"},
    "reviewing": {"completed", "failed"},
    "editing": {"rendering", "failed", "completed"},
    "needs_user": {"planning", "approved", "failed"},
    "completed": {"planning", "generating", "rendering", "editing"},
    "failed": {"planning", "approved", "generating", "rendering", "editing"},
}


# Instructions for headless pipeline jobs. Each one starts one agent-loop turn;
# the model plans, builds, edits, renders, and repairs with the same inline
# tools the interactive workspace chat uses.
def plan_instruction() -> str:
    """The planning turn instruction."""
    return (
        "Plan this analysis. First inspect the uploaded study inputs "
        "(inspect_project, inspect_table, check_design_matrix as needed). "
        "If the study design is genuinely ambiguous, ask_user with concrete options. "
        "Otherwise call set_plan with the complete analysis plan for this research question. "
        "The project is always written from scratch — never use, copy, or reference "
        "any ReportPack template."
    )


PLAN_INSTRUCTION = plan_instruction()

BUILD_INSTRUCTION = (
    "Build the report now from the approved plan. First check what already exists: "
    "inspect_project and the workspace file list are durable, and earlier edits are "
    "journaled — if files were already adapted or scripts already ran, continue from "
    "that state instead of re-reading and redoing everything. "
    "Write the Quarto project from scratch (_quarto.yml and analysis pages) and "
    "ground methods with search_bioc_books when useful. "
    "Run the data and analysis R steps (run_r_script) to verify they work, then "
    "render_report. If the render fails, read the errors, fix the source, and render "
    "again. Finish with validate_report and fix the findings that matter."
)




def edit_instruction(instruction: str) -> str:
    return (
        f"Apply this edit to the project: {instruction.strip()} "
        "Then render_report and verify the change landed."
    )


@task_decorator
def run_agent_job(*args, instruction: str = "", job_kind: str = "generate", chat_mode: str = "build", **kwargs):
    """Run one headless agent-loop turn for a pipeline job.

    This is the single execution path for plan/generate/render/edit jobs: the
    same native loop and inline tools as the workspace chat, driven by an
    instruction instead of a chat message.
    """
    project_id, job_id = _parse_task_args(args)
    if not str(instruction or "").strip():
        raise ValueError("run_agent_job requires a non-empty instruction")

    db = _get_db_session()
    try:
        from app.models.project import Job, Project, ProjectMessage, UploadedFile
        from app.schemas.schemas import WorkspaceAgentRequest
        from app.services.agent_runtime import record_agent_action, record_project_message, set_agent_state
        from app.services.llm import resolve_target
        from app.services.provider_guard import active_provider_block, provider_error_from_block
        from app.services.workspace_agent import stream_workspace_agent
        from app.services.workspace_handlers import (
            make_inline_action_handler,
            make_knowledge_search_handler,
            make_plan_handler,
            make_render_handler,
        )

        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise ValueError(f"Project {project_id} not found")

        agent_provider, _ = resolve_target("agent")
        provider_block = active_provider_block(project, agent_provider or settings.llm_provider)
        if provider_block is not None:
            raise provider_error_from_block(provider_block)

        # The loop's typed tools resolve relative paths against the project
        # directory, so stage the workspace before the turn starts. Idempotent:
        # already-staged files are kept, and edits stay untouched.
        project_dir = Path(settings.projects_dir) / str(project.id)
        project_dir.mkdir(parents=True, exist_ok=True)
        if not project.project_dir:
            project.project_dir = str(project_dir)
        _stage_uploaded_files(project_dir, db.query(UploadedFile).filter(UploadedFile.project_id == project_id).all())
        db.commit()

        job = db.query(Job).filter(Job.id == job_id).first()
        _update_job(db, job_id, status="running", progress=[{"step": "agent", "status": "running"}])

        agent_memory = dict(project.agent_memory or {})
        pending_guidance = [str(item.get("content") or "") for item in agent_memory.pop("pending_guidance", None) or [] if isinstance(item, dict)]
        if pending_guidance:
            agent_memory["pending_guidance"] = []
            project.agent_memory = agent_memory
            instruction = instruction + "\n\nQueued guidance from while the workspace was busy:\n- " + "\n- ".join(g for g in pending_guidance if g.strip())
        stored_answers = [item for item in agent_memory.get("clarifications") or [] if isinstance(item, dict)]
        if stored_answers:
            agent_memory.pop("clarifications", None)
            project.agent_memory = agent_memory
            instruction = (
                instruction
                + "\n\nThe user already answered these clarification questions:\n"
                + json.dumps(stored_answers, default=str)
            )

        user_message = record_project_message(
            db,
            project,
            "user",
            instruction,
            metadata={"job_id": job_id, "kind": job_kind},
        )
        history = (
            db.query(ProjectMessage)
            .filter(ProjectMessage.project_id == project_id, ProjectMessage.id != str(user_message.id))
            .order_by(ProjectMessage.created_at.asc())
            .all()
        )
        db.commit()

        request = WorkspaceAgentRequest(message=instruction, chat_mode=chat_mode)
        progress_log: list[dict] = []

        def _job_cancelled() -> bool:
            try:
                db.refresh(job)
                return job.status == "cancelled"
            except Exception:
                return False

        final_text = ""
        final_metadata: dict = {}

        async def drive() -> None:
            nonlocal final_text, final_metadata
            if settings.agent_backend == "opencode":
                from app.services.opencode_client import stream_opencode

                collected_tokens = []
                last_error = ""
                async for event in stream_opencode(
                    project_dir=project_dir,
                    instruction=instruction,
                    provider=agent_provider or settings.llm_provider,
                    model=settings.llm_model,
                ):
                    event_type = str(event.get("type") or "")
                    if event_type in {"tool_started", "action_event"}:
                        entry = {
                            "step": str(event.get("tool") or event.get("reason") or "action"),
                            "status": "running" if event_type == "tool_started" else "completed",
                            "detail": str(event.get("reason") or (event.get("event") or {}).get("summary") or "")[:200],
                        }
                        progress_log.append(entry)
                        _update_job(db, job_id, progress=progress_log)
                    elif event_type == "token":
                        collected_tokens.append(str(event.get("token") or ""))
                    elif event_type == "error":
                        last_error = str(event.get("error") or "")
                        entry = {"step": "error", "status": "failed", "detail": last_error[:200]}
                        progress_log.append(entry)
                        _update_job(db, job_id, progress=progress_log)
                final_text = "".join(collected_tokens).strip() or last_error
            else:
                async for event in stream_workspace_agent(
                    project,
                    request,
                    history,
                    inline_action_handler=make_inline_action_handler(db, project),
                    knowledge_search_handler=make_knowledge_search_handler(db),
                    render_handler=make_render_handler(db, project),
                    plan_handler=make_plan_handler(db, project),
                    cancel_check=_job_cancelled,
                ):
                    event_type = str(event.get("type") or "")
                    if event_type in {"tool_started", "tool_completed", "action_event", "question", "wait"}:
                        entry = {
                            "step": str(event.get("tool") or event.get("title") or event_type),
                            "status": "running" if event_type == "tool_started" else "completed",
                            "detail": str(event.get("summary") or event.get("reason") or "")[:200],
                        }
                        progress_log.append(entry)
                        _update_job(db, job_id, progress=progress_log)
                    elif event_type == "final":
                        final_text = str(event.get("message") or "")
                        final_metadata = {
                            key: value
                            for key, value in (event.items() if isinstance(event, dict) else [])
                            if key in {"awaiting_answer", "budget"}
                        }

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(drive())
        except Exception as exc:
            logger.exception("Agent job failed for project %s", project_id)
            _update_job(db, job_id, status="failed", error=str(exc))
            try:
                project.status = "failed"
                db.commit()
                set_agent_state(db, project, "failed", "Agent job failed")
                record_agent_action(db, project, job_kind, "failed", str(exc), job_id=job_id)
            except Exception:
                pass
            raise
        finally:
            loop.close()

        cancelled = _job_cancelled()

        if not cancelled:
            record_project_message(
                db,
                project,
                "assistant",
                final_text or "The run finished without a summary.",
                metadata={"job_id": job_id, "kind": job_kind, **(final_metadata or {})},
            )
            question = (final_metadata or {}).get("awaiting_answer")
            if isinstance(question, dict) and question.get("question"):
                # Bridge ask_user to the clarifications contract the Plan UI reads.
                memory = dict(project.agent_memory or {})
                memory["pending_clarifications"] = {
                    "message": "The agent needs a decision before continuing.",
                    "questions": [
                        {
                            "id": str(question.get("id") or "question-1"),
                            "prompt": str(question.get("question")),
                            "options": [str(o) for o in (question.get("options") or [])],
                            "multiple": bool(question.get("multiple")),
                            "allow_custom": True,
                        }
                    ],
                }
                project.agent_memory = memory
                if validate_status_transition(project.status, "needs_clarification"):
                    project.status = "needs_clarification"
        _update_job(
            db,
            job_id,
            status="cancelled" if cancelled else "completed",
            progress=progress_log + [{"step": "agent", "status": "completed"}],
        )

        # A successful planning turn advances the pipeline: mark the plan as
        # ready and, for auto-build projects, chain straight into the build so
        # "Build" mode really runs through without user intervention.
        if (
            not cancelled
            and job_kind == "plan"
            and project.analysis_plan
            and not isinstance((final_metadata or {}).get("awaiting_answer"), dict)
            and validate_status_transition(project.status, "planned")
        ):
            project.status = "planned"
            db.commit()
            if project.auto_build:
                build_job = Job(project_id=str(project.id), job_type="generate", status="pending")
                db.add(build_job)
                project.status = "generating"
                db.commit()
                db.refresh(build_job)
                set_agent_state(db, project, "generating", "Building the report with the agent loop")
                record_agent_action(
                    db,
                    project,
                    "generate",
                    "started",
                    "Auto-build: building the report from the plan",
                    job_id=str(build_job.id),
                )
                db.commit()
                if settings.task_backend.lower() == "celery":
                    run_agent_job.delay(
                        str(project.id), str(build_job.id),
                        instruction=BUILD_INSTRUCTION, job_kind="generate",
                    )
                else:
                    # Background backend already runs off the request thread,
                    # so the build can run inline in this same worker.
                    run_agent_job(
                        str(project.id), str(build_job.id),
                        instruction=BUILD_INSTRUCTION, job_kind="generate",
                    )
            else:
                set_agent_state(db, project, "idle", "Plan ready for review")

        from app.services.provider_guard import clear_provider_block

        clear_provider_block(project, settings.llm_provider)
        db.commit()
        return {
            "status": "cancelled" if cancelled else "completed",
            "job_id": job_id,
            "final": final_text,
        }
    finally:
        db.close()


def validate_status_transition(current: str | None, target: str) -> bool:
    """Validate whether a project status transition is permitted."""
    if not current or current == target:
        return True
    allowed = ALLOWED_TRANSITIONS.get(current, set())
    return target in allowed


def _get_db_session():
    """Create a database session for use in background tasks."""
    from app.database import SessionLocal
    return SessionLocal()


def _update_job(db, job_id: str | None, **kwargs):
    """Update a job record and push a live workspace event."""
    if not job_id:
        return
    from app.models.project import Job
    from app.services.job_events import publish_project_event

    job = db.query(Job).filter(Job.id == job_id).first()
    if job:
        for k, v in kwargs.items():
            setattr(job, k, v)
        job.updated_at = datetime.now(timezone.utc)
        db.commit()
        if str(job.status or "").lower() in {"completed", "failed", "cancelled"}:
            try:
                from app.services.agent_plans import mark_dependency_complete

                mark_dependency_complete(
                    db,
                    dependency_kind="job",
                    dependency_id=str(job.id),
                    dependency_status=str(job.status),
                    result={"status": job.status, "error": job.error, "progress": job.progress},
                )
                db.commit()
                from app.services.agent_continuations import dispatch_ready_continuations

                dispatch_ready_continuations(
                    db,
                    dependency_kind="job",
                    dependency_id=str(job.id),
                )
            except Exception:
                logger.exception("Could not advance continuation plan for job %s", job.id)
        publish_project_event(
            str(job.project_id),
            {
                "job_id": str(job.id),
                "job_type": job.job_type,
                "job_status": job.status,
            },
        )


def _stage_uploaded_files(project_dir: str | Path, files) -> list[str]:
    """Expose protected upload copies under the agent's project-local data/ tree.

    OpenHands is intentionally confined to ``project_dir``. File summaries alone
    are not enough: inspection tools need the corresponding bytes inside that
    boundary before planning begins. Staging is idempotent so generation can
    call the same helper without recopying unchanged inputs.
    """
    base = Path(project_dir).resolve()
    data_dir = base / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    staged: list[str] = []
    for file_record in files:
        raw_source = getattr(file_record, "file_path", None)
        if not raw_source:
            continue
        source = Path(str(raw_source))
        display_name = str(getattr(file_record, "original_name", None) or source.name)
        filename = Path(display_name.replace("\\", "/")).name
        if not filename or filename in {".", ".."}:
            filename = source.name
        if not source.is_file() or source.is_symlink():
            raise FileNotFoundError(f"Uploaded input is unavailable: {filename}")
        destination = data_dir / filename
        unchanged = False
        if destination.is_file() and not destination.is_symlink():
            source_stat = source.stat()
            destination_stat = destination.stat()
            unchanged = (
                source_stat.st_size == destination_stat.st_size
                and source_stat.st_mtime_ns == destination_stat.st_mtime_ns
            )
        if not unchanged:
            shutil.copy2(source, destination)
        staged.append(destination.relative_to(base).as_posix())
    return staged


def _parse_task_args(args):
    """Extract project_id and job_id cleanly whether called as (self, proj_id, job_id) or (proj_id, job_id)."""
    if len(args) >= 3:
        # Bound Celery task invocation: (self, project_id, job_id)
        return str(args[1]), str(args[2])
    elif len(args) == 2:
        # Direct / BackgroundTasks invocation: (project_id, job_id)
        return str(args[0]), str(args[1])
    elif len(args) == 1:
        return str(args[0]), None
    raise ValueError(f"Invalid positional arguments for task: {args}")


@task_decorator
def resume_agent_continuation(*args):
    """Resume a claimed Workspace or Note agent run after async completion."""
    if len(args) >= 2:
        run_id = str(args[1])
    elif len(args) == 1:
        run_id = str(args[0])
    else:
        raise ValueError("Missing agent run id for continuation")
    from app.services.agent_continuations import run_agent_continuation

    return asyncio.run(run_agent_continuation(run_id))












@task_decorator
def run_recipe_execution(*args, **kwargs):
    """Execute one recipe and only its stale dependency closure."""
    project_id, job_id = _parse_task_args(args)
    recipe_id = str(kwargs.get("recipe_id") or "")

    db = _get_db_session()
    try:
        from app.models.project import Job, Project
        from app.services.agent_runtime import (
            record_agent_action,
            record_project_message,
            refresh_project_memory,
            set_agent_state,
        )
        from app.services.recipe_execution import invalidate_recipe_cache, run_recipe_target
        from app.services.reviewer import review_render_output

        project = db.query(Project).filter(Project.id == project_id).first()
        if not project or not project.project_dir:
            raise ValueError(f"Project {project_id} has no generated project directory")
        if not recipe_id:
            raise ValueError("Targeted recipe execution requires recipe_id")

        progress_log = []
        execution_logs = []

        def progress_callback(step_id: str, status: str, line: str):
            if line:
                execution_logs.append(line)
            entry = {
                "step": step_id,
                "status": status,
                "time": datetime.now(timezone.utc).isoformat(),
            }
            if line:
                entry["detail"] = line.splitlines()[0]
            existing = next(
                (item for item in reversed(progress_log) if item["step"] == step_id),
                None,
            )
            if existing:
                existing.update(entry)
            else:
                progress_log.append(entry)
            _update_job(
                db,
                job_id,
                status="running",
                progress=progress_log,
                logs="\n".join(execution_logs[-200:]),
            )

        set_agent_state(db, project, "rendering", f"Running targeted recipe {recipe_id}")
        record_agent_action(
            db,
            project,
            "recipe",
            "started",
            f"Running targeted recipe {recipe_id}",
            {"recipe_id": recipe_id},
            job_id=job_id,
        )

        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(
            run_recipe_target(
                project.project_dir,
                recipe_id,
                progress_callback=progress_callback,
            )
        )
        loop.close()

        if result["status"] == "completed":
            review = review_render_output(project.project_dir)
            result["review"] = review
            if review["status"] == "failed":
                result["status"] = "failed"
                error = review["summary"]
            else:
                error = None
                project.status = "completed"
                set_agent_state(db, project, "completed", f"Targeted recipe {recipe_id} is current")
                executed = result.get("executed_recipes") or []
                cache_hits = result.get("cache_hits") or []
                summary = (
                    f"Updated {recipe_id}. Executed {len(executed)} recipe node(s); "
                    f"reused {len(cache_hits)} cached node(s)."
                )
                record_agent_action(
                    db,
                    project,
                    "recipe",
                    "completed",
                    summary,
                    {
                        "recipe_id": recipe_id,
                        "executed_recipes": executed,
                        "cache_hits": cache_hits,
                    },
                    job_id=job_id,
                )
                record_project_message(
                    db,
                    project,
                    "assistant",
                    f"{summary} {review['summary']}.",
                    metadata={
                        "job_id": job_id,
                        "recipe_id": recipe_id,
                        "executed_recipes": executed,
                        "cache_hits": cache_hits,
                    },
                )
        else:
            error = json.dumps(result.get("errors") or [])

        if result["status"] != "completed":
            invalidate_recipe_cache(project.project_dir, recipe_id)
            project.status = "failed"
            set_agent_state(db, project, "failed", f"Targeted recipe {recipe_id} failed")
            record_agent_action(
                db,
                project,
                "recipe",
                "failed",
                f"Targeted recipe {recipe_id} failed",
                {"errors": result.get("errors")},
                job_id=job_id,
            )
            record_project_message(
                db,
                project,
                "assistant",
                f"The targeted {recipe_id} run failed. Failure details are available in the job log; ask the workspace agent to repair it.",
                metadata={"job_id": job_id, "recipe_id": recipe_id, "status": "failed"},
            )

        db.commit()
        jobs = db.query(Job).filter(Job.project_id == project_id).order_by(Job.created_at.desc()).all()
        refresh_project_memory(db, project, jobs=jobs)
        _update_job(
            db,
            job_id,
            status="completed" if result["status"] == "completed" else "failed",
            progress=progress_log,
            logs="\n".join(execution_logs),
            error=error,
        )
        return result
    except Exception as exc:
        logger.exception("Targeted recipe execution failed for project %s", project_id)
        _update_job(db, job_id, status="failed", error=str(exc))
        raise
    finally:
        db.close()@task_decorator




@task_decorator
def sync_bioc_knowledge(*args):
    """Synchronise the curated QMD Bioconductor knowledge catalog."""
    if not settings.bioc_knowledge_sync_enabled:
        return {"status": "disabled"}
    from app.database import SessionLocal
    from app.services.bioc_knowledge import sync_catalog

    db = SessionLocal()
    try:
        return sync_catalog(
            db,
            settings.bioc_knowledge_catalog_path,
            storage_root=settings.bioc_knowledge_storage_dir,
            channels=("stable", "preview") if settings.bioc_knowledge_sync_preview_enabled else ("stable",),
        )
    finally:
        db.close()




