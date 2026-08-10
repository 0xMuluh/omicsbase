"""Dispatch durable agent continuations after asynchronous work completes.

The agent stream is an HTTP transport, but an asynchronous tool is part of a
larger logical turn. This module bridges the two: the job/execution task marks
a continuation ready, then a small worker re-enters the existing stream
endpoint with the original idempotency key. The endpoint appends the resumed
events to the same AgentRun so reconnects and replay remain lossless.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from app.config import settings
from app.models.runs import AgentRun
from app.services.agent_plans import (
    DEAD_LETTER,
    FAILED,
    READY,
    RUNNING,
    attach_continuation_plan,
    get_continuation_plan,
    mark_continuation_running,
)
from app.services.agent_runs import append_run_event, is_run_task_active

logger = logging.getLogger(__name__)


def _candidate_runs(
    db: Session,
    *,
    run_id: str | None = None,
    dependency_kind: str | None = None,
    dependency_id: str | None = None,
) -> list[AgentRun]:
    query = db.query(AgentRun).filter(AgentRun.continuation_status.in_([READY, FAILED]))
    if run_id:
        query = query.filter(AgentRun.id == str(run_id))
    if dependency_kind:
        query = query.filter(AgentRun.continuation_dependency_kind == str(dependency_kind))
    if dependency_id:
        query = query.filter(AgentRun.continuation_dependency_id == str(dependency_id))
    runs = query.order_by(AgentRun.created_at.asc()).with_for_update().all()
    result: list[AgentRun] = []
    for run in runs:
        plan = get_continuation_plan(run)
        if not plan or plan.get("status") not in {READY, FAILED}:
            continue
        if dependency_kind and str(plan.get("dependency_kind")) != str(dependency_kind):
            continue
        if dependency_id and str(plan.get("dependency_id")) != str(dependency_id):
            continue
        result.append(run)
    return result


def recover_interrupted_continuations(db: Session) -> int:
    """Re-open claims left behind by a worker or process crash."""
    recovered = 0
    legacy_backfilled = False
    legacy_runs = (
        db.query(AgentRun)
        .filter(AgentRun.run_metadata.isnot(None), AgentRun.continuation_status.is_(None))
        .all()
    )
    for legacy_run in legacy_runs:
        legacy_plan = get_continuation_plan(legacy_run)
        if legacy_plan:
            attach_continuation_plan(legacy_run, legacy_plan)
            legacy_backfilled = True

    runs = db.query(AgentRun).filter(AgentRun.continuation_status == RUNNING).all()
    for run in runs:
        plan = get_continuation_plan(run)
        if not plan or plan.get("status") != RUNNING:
            continue
        if str(run.status) in {"running", "waiting_tool"}:
            continue
        plan["status"] = READY if str(plan.get("dependency_status") or "").lower() in {"completed", "succeeded", "success"} else FAILED
        plan["recovered_at"] = datetime.now(timezone.utc).isoformat()
        attach_continuation_plan(run, plan)
        append_run_event(
            db,
            run,
            "continuation_recovered",
            {"action": plan.get("action"), "status": plan.get("status")},
            idempotency_key=f"continuation:recovered:{run.id}:{plan.get("attempts", 0)}",
        )
        recovered += 1
    if recovered or legacy_backfilled:
        db.commit()
    return recovered


def dispatch_ready_continuations(
    db: Session,
    *,
    run_id: str | None = None,
    dependency_kind: str | None = None,
    dependency_id: str | None = None,
) -> int:
    """Claim and dispatch ready plans without duplicating active workers.

    Claiming the plan before dispatch makes the durable row the cross-process
    dedupe mechanism. If a local agent worker is still consuming the result
    (the Note wait-enabled path), it is left READY; the stream worker retries
    the dispatch when it exits.
    """
    dispatched = 0
    for run in _candidate_runs(
        db,
        run_id=run_id,
        dependency_kind=dependency_kind,
        dependency_id=dependency_id,
    ):
        if run.cancel_requested or is_run_task_active(str(run.id)):
            continue
        plan = get_continuation_plan(run)
        if not plan or plan.get("status") not in {READY, FAILED}:
            continue
        previous_status = str(plan.get("status"))
        claimed = mark_continuation_running(run)
        if claimed is None:
            exhausted = get_continuation_plan(run)
            if exhausted and exhausted.get("status") == DEAD_LETTER:
                append_run_event(
                    db,
                    run,
                    "continuation_exhausted",
                    {"action": exhausted.get("action"), "attempts": exhausted.get("attempts", 0)},
                    idempotency_key=f"continuation:exhausted:{run.id}:{exhausted.get("attempts", 0)}",
                )
                db.commit()
            continue
        append_run_event(
            db,
            run,
            "continuation_dispatched",
            {
                "action": claimed.get("action"),
                "dependency_kind": claimed.get("dependency_kind"),
                "dependency_id": claimed.get("dependency_id"),
                "attempt": claimed.get("attempts"),
            },
            idempotency_key=(
                f"continuation:dispatch:{run.id}:{claimed.get('attempts', 0)}"
            ),
        )
        db.commit()
        try:
            _dispatch_continuation_worker(str(run.id))
        except Exception as exc:
            logger.exception("Could not dispatch continuation for run %s", run.id)
            db.rollback()
            failed_run = db.query(AgentRun).filter(AgentRun.id == str(run.id)).one_or_none()
            if failed_run is not None:
                failed_plan = get_continuation_plan(failed_run) or claimed
                failed_plan["status"] = previous_status
                failed_plan["dispatch_error"] = str(exc)[:2_000]
                attach_continuation_plan(failed_run, failed_plan)
                append_run_event(
                    db,
                    failed_run,
                    "continuation_dispatch_failed",
                    {"error": str(exc)[:2_000]},
                )
                db.commit()
            continue
        dispatched += 1
    return dispatched


def _dispatch_continuation_worker(run_id: str) -> None:
    """Use Celery when configured, otherwise start a local durable worker."""
    if settings.task_backend.lower() == "celery":
        from app.tasks.analysis import resume_agent_continuation

        resume_agent_continuation.delay(str(run_id))
        return
    if settings.task_backend.lower() != "background":
        raise RuntimeError(f"Unsupported task backend: {settings.task_backend}")
    thread = threading.Thread(
        target=_run_continuation_worker,
        args=(str(run_id),),
        name=f"agent-continuation-{run_id}",
        daemon=True,
    )
    thread.start()


def _run_continuation_worker(run_id: str) -> None:
    try:
        asyncio.run(run_agent_continuation(run_id))
    except Exception:
        logger.exception("Agent continuation worker failed for run %s", run_id)


async def run_agent_continuation(run_id: str) -> dict[str, Any]:
    """Resume one claimed run by consuming the existing stream endpoint."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        run = db.query(AgentRun).filter(AgentRun.id == str(run_id)).one_or_none()
        if run is None:
            return {"status": "missing", "run_id": str(run_id)}
        plan = get_continuation_plan(run)
        if not plan or plan.get("status") != RUNNING:
            return {"status": "not_claimed", "run_id": str(run_id)}
        payload: dict[str, Any] = run.input_payload if isinstance(run.input_payload, dict) else {}
        tenant_id = str(run.tenant_id)
        user_id = str(run.owner_id)
        background_tasks = BackgroundTasks()

        if run.surface == "workspace":
            from app.api.projects_agent import workspace_agent_stream
            from app.schemas.schemas import WorkspaceAgentRequest

            request_data = _model_validate(WorkspaceAgentRequest, payload)
            request_data = _copy_with_idempotency_key(request_data, str(run.idempotency_key))
            response = await workspace_agent_stream(
                str(run.project_id),
                request_data,
                background_tasks,
                db=db,
                tenant_id=tenant_id,
                user_id=user_id,
            )
        elif run.surface == "notes":
            from app.api.projects_notes import note_thread_turn
            from app.schemas.schemas import NoteThreadTurnRequest

            request_data = _model_validate(NoteThreadTurnRequest, payload)
            request_data = _copy_with_idempotency_key(request_data, str(run.idempotency_key))
            response = await note_thread_turn(
                str(run.note_thread_id),
                request_data,
                background_tasks,
                db=db,
                tenant_id=tenant_id,
                user_id=user_id,
            )
        else:
            raise ValueError(f"Unsupported continuation surface: {run.surface}")

        body_iterator = getattr(response, "body_iterator", None)
        if body_iterator is not None:
            async for _ in body_iterator:
                pass
        # The API normally lets Starlette run these after the response. A
        # continuation calls the handler directly, so run its newly queued
        # background work explicitly (important for task_backend=background).
        await background_tasks()
        return {"status": "completed", "run_id": str(run_id)}
    finally:
        db.close()


def _model_validate(model_type: Any, payload: dict[str, Any]) -> Any:
    if hasattr(model_type, "model_validate"):
        return model_type.model_validate(payload)
    return model_type.parse_obj(payload)


def _copy_with_idempotency_key(value: Any, key: str) -> Any:
    if hasattr(value, "model_copy"):
        return value.model_copy(update={"idempotency_key": key})
    return value.copy(update={"idempotency_key": key})


__all__ = [
    "dispatch_ready_continuations",
    "recover_interrupted_continuations",
    "run_agent_continuation",
]
