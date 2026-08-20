"""Async analysis tasks with optional Celery support."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
    "created": {"generating", "failed", "needs_clarification"},
    "needs_clarification": {"generating", "failed"},
    "generating": {"rendering", "completed", "failed", "needs_clarification", "editing", "created"},
    "rendering": {"completed", "failed", "generating", "editing"},
    "editing": {"rendering", "generating", "failed", "completed"},
    "completed": {"generating", "rendering", "editing"},
    "failed": {"generating", "rendering", "editing"},
}


# The agent's instruction is the user's own words (question/plan/notes).
# The system prompt (WORKSPACE_PREAMBLE) is the standing operating manual.
def user_instruction_for(project) -> str:
    """Return the user's own request text for a workspace turn."""
    for attr in ("question", "custom_plan_text", "notes"):
        value = getattr(project, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "Build the analytical report from the study data and plan in this project directory."


def _merge_step_usage(totals: dict[str, float], tokens: Any, cost: Any) -> None:
    """Accumulate OpenCode step_finish usage when present."""
    if isinstance(tokens, dict):
        for key in ("input", "input_tokens", "prompt", "prompt_tokens"):
            if tokens.get(key) is not None:
                totals["input_tokens"] += float(tokens[key])
        for key in ("output", "output_tokens", "completion", "completion_tokens"):
            if tokens.get(key) is not None:
                totals["output_tokens"] += float(tokens[key])
        for key in ("total", "total_tokens"):
            if tokens.get(key) is not None:
                totals["total_tokens"] += float(tokens[key])
    elif isinstance(tokens, (int, float)):
        totals["total_tokens"] += float(tokens)
    if cost is not None:
        try:
            totals["cost"] += float(cost)
        except (TypeError, ValueError):
            pass


def edit_instruction(instruction: str) -> str:
    return (
        f"Apply this edit to the project: {instruction.strip()} "
        "Then rerun the report (Quarto/R) and verify the change landed."
    )


_REPORT_REQUIRED_JOB_KINDS = frozenset({"generate", "render"})
_REPORT_ARTIFACT = Path("output/index.html")


@task_decorator
def run_agent_job(*args, instruction: str = "", job_kind: str = "generate", chat_mode: str = "build", **kwargs):
    """Run one OpenCode turn for a pipeline job.

    Workspace execution is OpenCode. This task stages uploads, streams the
    coding agent, and records job/project status. The agent decides methods
    and how to run R/Quarto.
    """
    project_id, job_id = _parse_task_args(args)
    if not str(instruction or "").strip():
        raise ValueError("run_agent_job requires a non-empty instruction")

    db = _get_db_session()
    try:
        from app.models.project import Job, Project, UploadedFile
        from app.services.agent_runtime import record_agent_action, record_project_message, set_agent_state
        from app.services.llm import resolve_target
        from app.services.opencode_client import stream_opencode
        from app.services.provider_guard import active_provider_block, provider_error_from_block

        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise ValueError(f"Project {project_id} not found")

        agent_provider, agent_model = resolve_target("agent")
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
        uploaded = db.query(UploadedFile).filter(UploadedFile.project_id == project_id).all()
        _stage_uploaded_files(project_dir, uploaded)
        db.commit()

        job = db.query(Job).filter(Job.id == job_id).first()
        _update_job(db, job_id, status="running", progress=[{"step": "agent", "status": "running"}])
        if validate_status_transition(project.status, "generating"):
            project.status = "generating"
            db.commit()

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
        db.commit()

        progress_log: list[dict] = []

        def _job_cancelled() -> bool:
            try:
                db.refresh(job)
                return job.status == "cancelled"
            except Exception:
                return False

        final_text = ""
        final_metadata: dict = {}
        usage_totals = {
            "input_tokens": 0.0,
            "output_tokens": 0.0,
            "total_tokens": 0.0,
            "cost": 0.0,
        }

        async def drive() -> None:
            nonlocal final_text, final_metadata
            opencode_ok = True
            last_error = ""
            collected_tokens: list[str] = []

            async for event in stream_opencode(
                project_dir=project_dir,
                instruction=instruction,
                provider=agent_provider or settings.llm_provider,
                model=agent_model or settings.llm_model,
                cancel_check=_job_cancelled,
            ):
                if _job_cancelled():
                    break
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
                elif event_type == "step_completed":
                    _merge_step_usage(
                        usage_totals,
                        event.get("tokens"),
                        event.get("cost"),
                    )
                elif event_type == "cancelled":
                    break
                elif event_type == "error":
                    last_error = str(event.get("error") or "")
                    opencode_ok = False
                    entry = {"step": "error", "status": "failed", "detail": last_error[:200]}
                    progress_log.append(entry)
                    _update_job(db, job_id, progress=progress_log)
                elif event_type == "final":
                    final_text = str(event.get("message") or "")
                    if event.get("ok") is False:
                        opencode_ok = False
                    if event.get("error"):
                        last_error = str(event.get("error") or last_error)
                    final_metadata = {
                        key: value
                        for key, value in event.items()
                        if key in {"awaiting_answer", "budget", "reasoning", "error"}
                    }
                elif event_type == "question":
                    final_metadata["awaiting_answer"] = {
                        "question": event.get("question"),
                        "options": event.get("options") or [],
                        "multiple": bool(event.get("multiple")),
                    }

            if not final_text:
                final_text = "".join(collected_tokens).strip() or last_error
            if last_error and not opencode_ok:
                final_metadata.setdefault("error", last_error)

            final_metadata["ok"] = opencode_ok and not _job_cancelled()
            if usage_totals["input_tokens"] or usage_totals["output_tokens"] or usage_totals["total_tokens"]:
                final_metadata["usage"] = {
                    key: int(value) if key != "cost" else value
                    for key, value in usage_totals.items()
                    if value
                }
            if last_error and not final_metadata.get("error"):
                final_metadata["error"] = last_error

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
        db.refresh(project)
        pending = ((project.agent_memory or {}).get("pending_clarifications") or None)
        question = (final_metadata or {}).get("awaiting_answer")
        awaiting_clarification = bool(pending) or (
            isinstance(question, dict) and bool(question.get("question"))
        )

        # OpenCode success only means that the agent stopped without an
        # API/runtime error. Report-producing jobs also require the published
        # report artifact. Clarification turns remain intentionally incomplete
        # and continue through the existing question flow.
        report_path = project_dir / _REPORT_ARTIFACT
        if (
            not cancelled
            and final_metadata.get("ok") is True
            and job_kind in _REPORT_REQUIRED_JOB_KINDS
            and not awaiting_clarification
            and not report_path.is_file()
        ):
            artifact_error = "Required completion artifact missing: output/index.html"
            final_metadata["ok"] = False
            final_metadata["error"] = artifact_error
            progress_log.append({"step": "artifact", "status": "failed", "detail": artifact_error})

        failed = (not cancelled) and final_metadata.get("ok") is False

        if cancelled:
            has_workspace = (project_dir / "code").is_dir() or (project_dir / "output").is_dir()
            target_status = "completed" if has_workspace else "created"
            if validate_status_transition(project.status, target_status):
                project.status = target_status
            set_agent_state(db, project, "idle", "Run cancelled")
        elif not cancelled:
            record_project_message(
                db,
                project,
                "assistant",
                final_text or "The run finished without a summary.",
                metadata={"job_id": job_id, "kind": job_kind, **(final_metadata or {})},
            )
            if pending or (isinstance(question, dict) and question.get("question")):
                if isinstance(question, dict) and question.get("question") and not pending:
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
            elif failed:
                if validate_status_transition(project.status, "failed"):
                    project.status = "failed"
                fail_detail = str(final_metadata.get("error") or final_text or "OpenCode failed")
                set_agent_state(db, project, "failed", fail_detail[:200])
                record_agent_action(db, project, job_kind, "failed", fail_detail, job_id=job_id)
            elif validate_status_transition(project.status, "completed"):
                project.status = "completed"
                set_agent_state(db, project, "idle", "OpenCode finished this workspace turn")
        job_status = "cancelled" if cancelled else ("failed" if failed else "completed")
        _update_job(
            db,
            job_id,
            status=job_status,
            error=(str(final_metadata.get("error") or final_text) if failed else None),
            progress=progress_log
            + [{"step": "agent", "status": job_status}]
            + ([{"step": "usage", "status": "completed", "detail": json.dumps(final_metadata.get("usage") or {})}] if final_metadata.get("usage") else []),
        )

        from app.services.provider_guard import clear_provider_block

        clear_provider_block(project, settings.llm_provider)
        db.commit()
        return {
            "status": job_status,
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




