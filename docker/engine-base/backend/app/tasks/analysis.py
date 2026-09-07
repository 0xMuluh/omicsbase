"""Async analysis tasks with optional Celery support."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import uuid
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
        },
        "reconcile-orphaned-agent-runs": {
            "task": "app.tasks.analysis.reconcile_orphaned_agent_runs",
            "schedule": float(settings.agent_reconciliation_interval_seconds),
        },
        "cleanup-agent-run-history": {
            "task": "app.tasks.analysis.cleanup_agent_run_history",
            "schedule": float(settings.agent_run_retention_interval_hours) * 3600,
        },
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
    "rendering": {"completed", "failed", "generating", "editing", "needs_clarification"},
    "editing": {"rendering", "generating", "failed", "completed", "needs_clarification"},
    "completed": {"generating", "rendering", "editing", "needs_clarification"},
    "failed": {"generating", "rendering", "editing", "needs_clarification"},
}


# The agent's instruction is the user's own words (question/plan/notes).
# The system prompt (WORKSPACE_PREAMBLE) is the standing operating manual.
def user_instruction_for(project) -> str:
    """Return only the user's request; an absent request stays empty.

    Callers that queue a legacy pipeline job must reject an empty value rather
    than inventing a synthetic user message for the agent or transcript.
    """
    for attr in ("question", "custom_plan_text", "notes"):
        value = getattr(project, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _merge_step_usage(totals: dict[str, float], tokens: Any, cost: Any) -> None:
    """Accumulate one provider usage record without alias double-counting."""
    if isinstance(tokens, dict):
        def first_number(keys: tuple[str, ...]) -> tuple[float, bool]:
            for key in keys:
                value = tokens.get(key)
                if value is not None:
                    try:
                        return float(value), True
                    except (TypeError, ValueError):
                        return 0.0, True
            return 0.0, False

        input_tokens, _ = first_number(("input_tokens", "input", "prompt_tokens", "prompt"))
        output_tokens, _ = first_number(("output_tokens", "output", "completion_tokens", "completion"))
        total_tokens, has_total = first_number(("total_tokens", "total"))
        totals["input_tokens"] += input_tokens
        totals["output_tokens"] += output_tokens
        totals["total_tokens"] += total_tokens if has_total else input_tokens + output_tokens
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


def execution_instruction_for(instruction: str, job_kind: str) -> str:
    """Add only generic execution guidance; the agent owns task completion.

    This deliberately does not prescribe page names, file layouts, methods, or
    a report manifest. The request and the agent's observations determine what
    should be produced; the platform only owns execution lifecycle.
    """
    request = str(instruction or "").strip()
    if job_kind not in _REPORT_REQUIRED_JOB_KINDS:
        return request
    return request + "\n\n" + """
This is an autonomous workspace execution turn. If a material user decision
blocks the work, use the runtime's structured question capability when available and stop after asking
it. Otherwise inspect the project and inputs, implement the requested work,
run the relevant computations and rendering, repair errors, and verify the
result before deciding that the user's request is complete. The appropriate
artifacts and report structure are determined by the request and the study;
do not wait for a platform-prescribed filename or chapter list. Persist useful
work as you go. Do not stop at a plan when the request asks you to build.
""".strip()


def _session_factory_for(db):
    """Build short-lived sessions on the same engine as the task session."""
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(autocommit=False, autoflush=False, bind=db.get_bind())


def _job_or_run_cancelled(session_factory, job_id: str | None, run_id: str | None) -> bool:
    """Read cancellation durably, translating one legacy Job stop if needed.

    A linked Job is normally only a projection. The fallback translation keeps
    an old client or stale deployment that writes ``Job.status`` from leaving
    the actual AgentRun alive; after translation, AgentRun remains authoritative.
    """
    check_db = session_factory()
    try:
        from app.models.project import Job
        from app.models.runs import AgentRun

        if run_id:
            run = check_db.query(AgentRun).filter(AgentRun.id == str(run_id)).one_or_none()
            if run is None:
                return False
            if run.cancel_requested or str(run.status or "") in {"cancel_requested", "cancelled"}:
                return True
            job = check_db.query(Job).filter(Job.id == str(job_id or "")).one_or_none()
            if job and str(job.status or "") in {"cancel_requested", "cancelled"}:
                # Do not append a lifecycle event from this polling path: a
                # worker may be appending a concurrent event sequence. The
                # canonical stream will record terminal cancellation.
                run.cancel_requested = True
                run.updated_at = datetime.now(timezone.utc)
                run.heartbeat_at = run.updated_at
                check_db.commit()
                return True
            return False
        if job_id:
            job = check_db.query(Job).filter(Job.id == str(job_id)).one_or_none()
            return bool(job and str(job.status or "") in {"cancel_requested", "cancelled"})
        return False
    except Exception as exc:
        check_db.rollback()
        logger.warning("Cancellation poll failed: %s", exc)
        return False
    finally:
        check_db.close()


def _touch_run(session_factory, run_id: str | None, *, step: int | None = None, metadata: dict | None = None) -> None:
    if not run_id:
        return
    from app.services.agent_runs import touch_agent_run

    touch_db = session_factory()
    try:
        touch_agent_run(touch_db, str(run_id), step=step, metadata=metadata)
        touch_db.commit()
    except Exception as exc:
        touch_db.rollback()
        logger.warning("Could not persist AgentRun heartbeat: %s", exc)
    finally:
        touch_db.close()


def _record_run_step(
    session_factory,
    run_id: str | None,
    *,
    step: int | None,
    tokens: Any,
    cost: Any,
    provider: str | None,
    model: str | None,
    usage_totals: dict[str, float],
) -> None:
    """Persist usage after each completed model step, not only at finalization."""
    if not run_id:
        return
    from app.services.agent_runs import record_run_telemetry, touch_agent_run

    raw = tokens if isinstance(tokens, dict) else {}
    def first_number(keys: tuple[str, ...]) -> int:
        for key in keys:
            value = raw.get(key)
            if value is not None:
                try:
                    return max(0, int(float(value)))
                except (TypeError, ValueError):
                    return 0
        return 0

    normalized = {
        "input_tokens": first_number(("input_tokens", "input", "prompt_tokens", "prompt")),
        "output_tokens": first_number(("output_tokens", "output", "completion_tokens", "completion")),
        "total_tokens": first_number(("total_tokens", "total")),
    }
    if not normalized["total_tokens"]:
        normalized["total_tokens"] = normalized["input_tokens"] + normalized["output_tokens"]

    step_db = session_factory()
    try:
        run = touch_agent_run(
            step_db,
            str(run_id),
            step=step,
            metadata={
                "usage_totals": {
                    key: (value if key == "cost" else int(value))
                    for key, value in usage_totals.items()
                }
            },
        )
        if run is not None:
            record_run_telemetry(
                step_db,
                run,
                kind="agent_step",
                operation="opencode_step",
                status="completed",
                input_tokens=normalized["input_tokens"] or None,
                output_tokens=normalized["output_tokens"] or None,
                total_tokens=normalized["total_tokens"] or None,
                cost_usd=float(cost) if cost is not None else None,
                provider=provider,
                model=model,
                metadata={"step": step, "provider_usage": raw},
            )
        step_db.commit()
    except Exception as exc:
        step_db.rollback()
        logger.warning("Could not persist step telemetry: %s", exc)
    finally:
        step_db.close()



def _finalize_agent_run(
    session_factory,
    run_id: str | None,
    *,
    status: str,
    message: str = "",
    error: str | None = None,
    usage_totals: dict[str, float] | None = None,
) -> None:
    """Finalize a linked run and project its compatibility Job atomically."""
    if not run_id:
        return
    from app.models.runs import AgentRun
    from app.services.agent_runs import (
        TERMINAL_RUN_STATUSES,
        sync_compatibility_jobs,
        transition_agent_run,
    )

    finalize_db = session_factory()
    try:
        run = finalize_db.query(AgentRun).filter(AgentRun.id == str(run_id)).one_or_none()
        if run is None:
            return
        # A terminal run is immutable. A late worker exception must not turn a
        # successful completion into a failure or cancellation.
        if run.status in TERMINAL_RUN_STATUSES:
            if usage_totals:
                metadata = dict(run.run_metadata or {})
                metadata["usage_totals"] = {
                    key: (value if key == "cost" else int(value))
                    for key, value in usage_totals.items()
                }
                run.run_metadata = metadata
            sync_compatibility_jobs(
                finalize_db,
                run,
                error=error if run.status == "failed" else None,
            )
            finalize_db.commit()
            return

        target = "cancelled" if run.cancel_requested or status == "cancelled" else (
            "paused" if status == "paused" else status
        )
        if run.status != target:
            transition_agent_run(
                finalize_db,
                run,
                target,
                event_type="run_cancelled" if target == "cancelled" else f"run_{target}",
                payload={"message": message[:1000], "error": error[:1000] if error else None},
            )
        metadata = dict(run.run_metadata or {})
        if usage_totals:
            metadata["usage_totals"] = {
                key: (value if key == "cost" else int(value))
                for key, value in usage_totals.items()
            }
        run.run_metadata = metadata
        run.result_payload = {
            "message": message[:4000],
            "error": error[:4000] if error else None,
            "status": target,
        }
        sync_compatibility_jobs(finalize_db, run, error=error)
        finalize_db.commit()
    except Exception as exc:
        finalize_db.rollback()
        logger.warning("Could not finalize AgentRun %s: %s", run_id, exc)
    finally:
        finalize_db.close()


@task_decorator
def run_agent_job(*args, instruction: str = "", job_kind: str = "generate", chat_mode: str = "build", **kwargs):
    """Run one compatibility pipeline Job through the canonical workspace executor.

    The Job remains a legacy response projection. Coding-agent execution, event
    persistence, telemetry, clarification state, and session reuse all live in
    stream_workspace_events.
    """
    project_id, job_id = _parse_task_args(args)
    agent_run_id = str(kwargs.get("agent_run_id") or "").strip() or None
    if not str(instruction or "").strip():
        raise ValueError("run_agent_job requires a non-empty instruction")

    db = _get_db_session()
    session_factory = None
    loop = None
    worker_task = None
    try:
        from app.models.project import Job, Project
        from app.models.runs import AgentRun
        from app.services.agent_runtime import record_agent_action, record_project_message
        from app.services.agent_runs import (
            cancel_agent_run,
            create_or_get_agent_run,
            register_run_task,
            sync_compatibility_jobs,
            sync_project_from_agent_run,
            transition_agent_run,
            unregister_run_task,
        )
        from app.services.llm import resolve_target
        from app.services.provider_guard import active_provider_block, clear_provider_block, provider_error_from_block

        project = db.query(Project).filter(Project.id == project_id).one_or_none()
        if project is None:
            raise ValueError(f"Project {project_id} not found")
        job = db.query(Job).filter(Job.id == job_id, Job.project_id == project_id).one_or_none()
        if job is None:
            raise ValueError(f"Job {job_id} not found")

        job_kind = str(job_kind or job.job_type or "generate").strip().lower()
        chat_mode = str(chat_mode or "build").strip().lower() or "build"
        session_factory = _session_factory_for(db)
        linked_run_id = agent_run_id or str(job.agent_run_id or "").strip() or None
        agent_run = None
        run_created = False
        if linked_run_id:
            agent_run = (
                db.query(AgentRun)
                .filter(
                    AgentRun.id == linked_run_id,
                    AgentRun.project_id == project_id,
                    AgentRun.tenant_id == str(project.tenant_id or "default_tenant"),
                )
                .one_or_none()
            )
        if agent_run is None:
            agent_run, run_created = create_or_get_agent_run(
                db,
                tenant_id=str(project.tenant_id or "default_tenant"),
                owner_id=str(project.owner_id or "default_user"),
                surface="workspace",
                kind=f"pipeline_{job_kind}",
                idempotency_scope=f"pipeline:{project.id}:{job_kind}",
                idempotency_key=str(job.id),
                request_payload={
                    "project_id": project_id,
                    "job_id": str(job.id),
                    "job_type": job_kind,
                    "instruction": str(instruction),
                },
                project_id=project_id,
                run_metadata={
                    "job_id": str(job.id),
                    "job_type": job_kind,
                    "instruction": str(instruction),
                },
            )
        agent_run_id = str(agent_run.id)
        job.agent_run_id = agent_run_id
        run_metadata = dict(agent_run.run_metadata or {})
        run_metadata.update({
            "job_id": str(job.id),
            "job_type": job_kind,
            "instruction": str(instruction),
        })
        agent_run.run_metadata = run_metadata

        if (
            str(job.status or "").lower() in {"cancel_requested", "cancelled"}
            or bool(agent_run.cancel_requested)
            or str(agent_run.status or "") in {"cancel_requested", "cancelled"}
        ):
            db.flush()
            agent_run = cancel_agent_run(
                db,
                agent_run,
                reason="job was cancelled before execution",
            )
        elif agent_run.status == "queued":
            transition_agent_run(
                db,
                agent_run,
                "running",
                event_type="run_started",
                payload={"job_id": str(job.id), "job_type": job_kind},
            )
        elif agent_run.status == "paused":
            transition_agent_run(
                db,
                agent_run,
                "running",
                event_type="run_resumed",
                payload={"job_id": str(job.id), "job_type": job_kind},
            )
        sync_compatibility_jobs(db, agent_run)
        db.commit()

        celery_task_id = ""
        if args and hasattr(args[0], "request"):
            celery_task_id = str(getattr(args[0].request, "id", "") or "").strip()
        if celery_task_id:
            _touch_run(session_factory, agent_run_id, metadata={"celery_task_id": celery_task_id})

        if _job_or_run_cancelled(session_factory, job_id, agent_run_id):
            _finalize_agent_run(
                session_factory,
                agent_run_id,
                status="cancelled",
                message="Agent job cancelled before execution",
            )
            _update_job(db, job_id, status="cancelled", error=None)
            db.rollback()
            project = db.query(Project).filter(Project.id == project_id).one()
            agent_run = db.query(AgentRun).filter(AgentRun.id == agent_run_id).one()
            sync_project_from_agent_run(db, agent_run, project, summary="Run cancelled")
            return {"status": "cancelled", "job_id": job_id, "final": ""}

        agent_provider, agent_model = resolve_target("agent")
        provider_block = active_provider_block(project, agent_provider or settings.llm_provider)
        if provider_block is not None:
            detail = str(provider_error_from_block(provider_block))
            _finalize_agent_run(
                session_factory,
                agent_run_id,
                status="failed",
                message="Provider is blocked",
                error=detail,
            )
            _update_job(db, job_id, status="failed", error=detail)
            db.rollback()
            project = db.query(Project).filter(Project.id == project_id).one()
            agent_run = db.query(AgentRun).filter(AgentRun.id == agent_run_id).one()
            sync_project_from_agent_run(db, agent_run, project, summary=detail)
            raise provider_error_from_block(provider_block)

        project_dir = Path(settings.projects_dir) / project_id
        project_dir.mkdir(parents=True, exist_ok=True)
        if not project.project_dir:
            project.project_dir = str(project_dir)
        db.commit()

        if not _job_or_run_cancelled(session_factory, job_id, agent_run_id):
            _update_job(db, job_id, status="running", progress=[])
            db.rollback()
            project = db.query(Project).filter(Project.id == project_id).one()
            agent_run = db.query(AgentRun).filter(AgentRun.id == agent_run_id).one()
            sync_project_from_agent_run(db, agent_run, project)

        agent_memory = dict(project.agent_memory or {})
        raw_guidance = agent_memory.pop("pending_guidance", None) or []
        pending_guidance = [
            str(item.get("content") or "")
            for item in raw_guidance
            if isinstance(item, dict) and str(item.get("content") or "").strip()
        ]
        if raw_guidance:
            agent_memory["pending_guidance"] = []
            project.agent_memory = agent_memory
            db.commit()
        if pending_guidance:
            instruction += "\n\nQueued guidance from while the workspace was busy:\n- " + "\n- ".join(pending_guidance)
        stored_answers = [
            item for item in (agent_memory.get("clarifications") or [])
            if isinstance(item, dict)
        ]
        if stored_answers:
            instruction += (
                "\n\nThe user already answered these clarification questions:\n"
                + json.dumps(stored_answers, default=str)
            )

        execution_instruction = execution_instruction_for(instruction, job_kind)
        user_message = record_project_message(
            db,
            project,
            "user",
            instruction,
            metadata={"job_id": job_id, "kind": job_kind, "agent_run_id": agent_run_id},
            cell_id=str(uuid.uuid4()),
            cell_type="agent",
            cell_revision=1,
            execution_id=agent_run_id,
        )

        progress_log: list[dict[str, Any]] = []
        final_text = ""
        final_metadata: dict[str, Any] = {}
        collected_tokens: list[str] = []
        usage_totals = {
            "input_tokens": 0.0,
            "output_tokens": 0.0,
            "total_tokens": 0.0,
            "cost": 0.0,
        }

        def job_cancelled() -> bool:
            return _job_or_run_cancelled(session_factory, job_id, agent_run_id)

        def record_run_message(
            message_db,
            message_project,
            role: str,
            content: str,
            *,
            kind: str = "message",
            metadata: dict[str, Any] | None = None,
        ):
            return record_project_message(
                message_db,
                message_project,
                role,
                content,
                kind=kind,
                metadata=metadata,
                cell_id=str(uuid.uuid4()),
                cell_revision=1,
                execution_id=agent_run_id,
            )

        def project_event(raw_event: Any) -> None:
            nonlocal final_text, final_metadata
            if isinstance(raw_event, str):
                try:
                    event = json.loads(raw_event)
                except (TypeError, ValueError):
                    return
            elif isinstance(raw_event, dict):
                event = dict(raw_event)
            else:
                return
            event_type = str(event.get("type") or "")
            changed = False
            if event_type == "token":
                token = str(event.get("token") or "")
                if token:
                    collected_tokens.append(token)
            elif event_type == "step_started":
                step = event.get("step")
                progress_log.append({
                    "step": f"model step {step}" if step is not None else "model step",
                    "status": "running",
                    "detail": f"Model step {step} started" if step is not None else "Model step started",
                })
                changed = True
            elif event_type == "tool_started":
                tool_name = str(event.get("tool") or "workspace tool").strip()
                progress_log.append({
                    "step": tool_name,
                    "status": "running",
                    "detail": str(event.get("reason") or f"Running {tool_name}")[:200],
                })
                changed = True
            elif event_type in {"tool_completed", "action_event"}:
                action = event.get("event") if isinstance(event.get("event"), dict) else {}
                target = action.get("target") if isinstance(action.get("target"), dict) else {}
                tool_name = str(event.get("tool") or action.get("title") or target.get("tool") or "workspace tool").strip()
                detail = str(event.get("summary") or action.get("summary") or action.get("title") or f"{tool_name} completed").strip()
                progress_log.append({"step": tool_name, "status": "completed", "detail": detail[:200]})
                changed = True
            elif event_type in {"step_completed", "usage"}:
                changed = True
            elif event_type == "error":
                detail = str(event.get("error") or "Coding agent error")
                progress_log.append({"step": "error", "status": "failed", "detail": detail[:200]})
                changed = True
            elif event_type == "final":
                final_text = str(event.get("message") or "")
                final_metadata = {
                    key: value
                    for key, value in event.items()
                    if key in {"ok", "awaiting_answer", "budget", "reasoning", "error", "finish", "cancelled", "usage"}
                }
                changed = True
            if changed:
                _update_job(db, job_id, progress=progress_log)

        async def drive() -> None:
            from app.schemas.schemas import WorkspaceAgentRequest
            from app.services.workspace_turn.events import stream_workspace_events

            stored_session_id = str((agent_run.run_metadata or {}).get("agent_session_id") or "").strip() or None
            request_data = WorkspaceAgentRequest(
                message=execution_instruction,
                chat_mode=chat_mode,
            )
            stream_source = stream_workspace_events(
                db=db,
                run=agent_run,
                project_id=project_id,
                tenant_id=str(project.tenant_id or "default_tenant"),
                project=project,
                data=request_data,
                user_message=user_message,
                worker_session_factory=session_factory,
                record_run_message=record_run_message,
                session_id=stored_session_id,
                fresh_session=bool(run_created and job_kind in {"generate", "render"}),
                cancel_check=job_cancelled,
            )
            try:
                async for raw_event in stream_source:
                    project_event(raw_event)
            finally:
                close_stream = getattr(stream_source, "aclose", None)
                if close_stream is not None:
                    await close_stream()

        loop = asyncio.new_event_loop()
        worker_task = loop.create_task(drive(), name=f"pipeline-agent-{agent_run_id}")
        register_run_task(agent_run_id, worker_task)
        try:
            loop.run_until_complete(worker_task)
        except asyncio.CancelledError:
            requested = job_cancelled()
            target = "cancelled" if requested else "paused"
            _finalize_agent_run(
                session_factory,
                agent_run_id,
                status=target,
                message="Agent job cancelled" if requested else "Agent job paused after interruption",
                error=None if requested else "Worker interrupted",
                usage_totals=usage_totals,
            )
            _update_job(
                db,
                job_id,
                status=target,
                error=None if requested else "Worker interrupted; run can be resumed.",
                progress=progress_log,
            )
            db.rollback()
            project = db.query(Project).filter(Project.id == project_id).one()
            agent_run = db.query(AgentRun).filter(AgentRun.id == agent_run_id).one()
            sync_project_from_agent_run(
                db,
                agent_run,
                project,
                summary="Run cancelled" if requested else None,
            )
            db.commit()
            return {"status": target, "job_id": job_id, "final": ""}
        except Exception as exc:
            logger.exception("Pipeline workspace execution failed for %s", project_id)
            _finalize_agent_run(
                session_factory,
                agent_run_id,
                status="failed",
                message="Agent job failed",
                error=str(exc),
                usage_totals=usage_totals,
            )
            _update_job(db, job_id, status="failed", error=str(exc), progress=progress_log)
            db.rollback()
            project = db.query(Project).filter(Project.id == project_id).one()
            agent_run = db.query(AgentRun).filter(AgentRun.id == agent_run_id).one()
            sync_project_from_agent_run(db, agent_run, project, summary=str(exc))
            record_agent_action(db, project, job_kind, "failed", str(exc), job_id=job_id)
            raise
        finally:
            unregister_run_task(agent_run_id, worker_task)
            loop.close()

        if not final_text:
            final_text = "".join(collected_tokens).strip()
        db.rollback()
        project = db.query(Project).filter(Project.id == project_id).one()
        agent_run = db.query(AgentRun).filter(AgentRun.id == agent_run_id).one()
        run_status = str(agent_run.status or "failed")
        if run_status not in {"completed", "failed", "cancelled", "paused"}:
            run_status = "cancelled" if job_cancelled() else "failed"
            _finalize_agent_run(
                session_factory,
                agent_run_id,
                status=run_status,
                message=final_text,
                error="Coding agent ended without a terminal run event",
                usage_totals=usage_totals,
            )
            db.rollback()
            agent_run = db.query(AgentRun).filter(AgentRun.id == agent_run_id).one()
            run_status = str(agent_run.status or run_status)

        pending = (project.agent_memory or {}).get("pending_clarifications")
        awaiting_clarification = run_status == "paused" and bool(pending)
        failed = run_status == "failed"
        cancelled = run_status == "cancelled"
        budget_metadata = (final_metadata or {}).get("budget")
        error_detail = str(
            (final_metadata or {}).get("error")
            or (budget_metadata.get("reason") if isinstance(budget_metadata, dict) else "")
        )
        if cancelled:
            project_summary = "Run cancelled"
        elif awaiting_clarification:
            project_summary = "Waiting for your decision"
        elif run_status == "paused":
            project_summary = "Run paused; retry to resume"
        elif failed:
            project_summary = error_detail or final_text or "Coding agent failed"
        else:
            project_summary = "Coding agent finished this workspace turn"

        sync_project_from_agent_run(
            db,
            agent_run,
            project,
            summary=project_summary,
        )
        if failed:
            detail = error_detail or final_text or "Coding agent failed"
            record_agent_action(db, project, job_kind, "failed", detail, job_id=job_id)

        status = "cancelled" if cancelled else "failed" if failed else "paused" if run_status == "paused" else "completed"
        _update_job(
            db,
            job_id,
            status=status,
            error=error_detail or (final_text if failed else None),
            progress=progress_log,
        )
        clear_provider_block(project, agent_provider or settings.llm_provider)
        db.commit()
        return {"status": status, "job_id": job_id, "final": final_text}
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
    """Project a linked AgentRun into the legacy Job and publish its snapshot."""
    if not job_id:
        return
    from sqlalchemy.orm import sessionmaker

    from app.models.project import Job
    from app.models.runs import AgentRun
    from app.services.job_events import publish_project_event

    try:
        bind = db.get_bind()
    except Exception:
        logger.exception("Could not resolve database bind while updating job %s", job_id)
        return

    update_db = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=bind,
    )()
    snapshot = None
    try:
        job = update_db.query(Job).filter(Job.id == str(job_id)).first()
        if job is None:
            return
        now = datetime.now(timezone.utc)
        run = None
        if job.agent_run_id:
            run = update_db.query(AgentRun).filter(AgentRun.id == str(job.agent_run_id)).one_or_none()
        if run is not None:
            from app.services.agent_runs import sync_compatibility_jobs

            projected_progress = kwargs.get("progress") if "progress" in kwargs else None
            sync_compatibility_jobs(
                update_db,
                run,
                error=kwargs.get("error"),
                progress=projected_progress,
            )
            kwargs = dict(kwargs)
            if projected_progress is not None and "logs" not in kwargs:
                progress_lines = [
                    str(item.get("detail") or item.get("step") or "")
                    for item in projected_progress
                    if isinstance(item, dict) and (item.get("detail") or item.get("step"))
                ]
                if progress_lines:
                    kwargs["logs"] = "\n".join(progress_lines)[-12_000:]
            # AgentRun owns these fields; this function only carries over
            # compatibility-only fields such as logs.
            kwargs.pop("status", None)
            kwargs.pop("error", None)
            kwargs.pop("progress", None)
        else:
            current_status = str(job.status or "").lower()
            if current_status == "cancelled":
                return
            requested_status = str(kwargs.get("status") or "").lower()
            if current_status == "cancel_requested" and requested_status not in {"", "cancel_requested", "cancelled"}:
                kwargs = dict(kwargs)
                kwargs.pop("status", None)
            if "progress" in kwargs and "logs" not in kwargs:
                progress_items = kwargs.get("progress") or []
                progress_lines = [
                    str(item.get("detail") or item.get("step") or "")
                    for item in progress_items
                    if isinstance(item, dict) and (item.get("detail") or item.get("step"))
                ]
                if progress_lines:
                    kwargs["logs"] = "\n".join(progress_lines)[-12_000:]

        for key, value in kwargs.items():
            setattr(job, key, value)
        status = str(job.status or "").lower()
        if status == "running" and job.started_at is None:
            job.started_at = now
        if status in {"completed", "failed", "cancelled", "paused"}:
            if job.finished_at is None:
                job.finished_at = now
        elif status in {"pending", "cancel_requested"}:
            job.finished_at = None
        job.heartbeat_at = now
        job.updated_at = now
        update_db.commit()
        snapshot = {
            "project_id": str(job.project_id),
            "job_id": str(job.id),
            "job_type": job.job_type,
            "job_status": job.status,
            "agent_run_id": str(job.agent_run_id) if job.agent_run_id else None,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "heartbeat_at": job.heartbeat_at,
        }
        if str(job.status or "").lower() in {"completed", "failed", "cancelled"}:
            try:
                from app.services.agent_plans import mark_dependency_complete

                mark_dependency_complete(
                    update_db,
                    dependency_kind="job",
                    dependency_id=str(job.id),
                    dependency_status=str(job.status),
                    result={"status": job.status, "error": job.error, "progress": job.progress},
                )
                update_db.commit()
                from app.services.agent_continuations import dispatch_ready_continuations

                dispatch_ready_continuations(
                    update_db,
                    dependency_kind="job",
                    dependency_id=str(job.id),
                )
                update_db.commit()
            except Exception:
                update_db.rollback()
                logger.exception("Could not advance continuation plan for job %s", job.id)
    except Exception:
        update_db.rollback()
        logger.exception("Could not persist job %s update", job_id)
    finally:
        update_db.close()

    if snapshot:
        publish_project_event(snapshot["project_id"], snapshot)


def _stage_uploaded_files(project_dir: str | Path, files) -> list[str]:
    """Expose protected upload copies under the agent's project-local data/ tree.

    The agent is intentionally confined to ``project_dir``. File summaries alone
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


@task_decorator
def reconcile_orphaned_agent_runs(*args):
    """Pause durable runs and jobs whose owning worker stopped heartbeating."""
    from app.database import SessionLocal
    from app.services.agent_runs import pause_stale_agent_runs, reconcile_stale_jobs

    db = SessionLocal()
    try:
        runs = pause_stale_agent_runs(
            db, stale_after_seconds=settings.agent_run_stale_after_seconds
        )
        jobs = reconcile_stale_jobs(
            db, stale_after_seconds=settings.agent_run_stale_after_seconds
        )
        return {"runs_paused": runs, "jobs_reconciled": jobs}
    finally:
        db.close()



def cleanup_agent_run_history(*args):
    """Purge old terminal AgentRun events and telemetry on a schedule."""
    if int(getattr(settings, "agent_run_retention_days", 0) or 0) <= 0:
        return {
            "runs_deleted": 0,
            "events_deleted": 0,
            "telemetry_deleted": 0,
            "jobs_detached": 0,
        }
    from app.database import SessionLocal
    from app.services.agent_runs import purge_expired_agent_runs

    db = SessionLocal()
    try:
        return purge_expired_agent_runs(
            db,
            retention_days=settings.agent_run_retention_days,
            batch_size=settings.agent_run_retention_batch_size,
        )
    finally:
        db.close()
