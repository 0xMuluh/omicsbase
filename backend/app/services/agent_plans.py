"""Durable continuation plans for asynchronous agent actions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

PLAN_KEY = "continuation_plan"
PLAN_VERSION = 1
WAITING = "waiting"
READY = "ready"
RUNNING = "running"
FAILED = "failed"
DONE = "done"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_json(value: Any, *, max_chars: int = 8_000) -> Any:
    try:
        encoded = json.dumps(value, default=str, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)[:max_chars]
    if len(encoded) <= max_chars:
        try:
            return json.loads(encoded)
        except json.JSONDecodeError:
            return encoded[:max_chars]
    return {"truncated": True, "preview": encoded[:max_chars]}


def build_continuation_plan(
    run: Any,
    *,
    action: str,
    dependency_kind: str,
    dependency_id: str,
    instruction: str,
    arguments: dict[str, Any] | None = None,
    dependency_status: str = "queued",
) -> dict[str, Any]:
    """Build the bounded state needed to continue a completed async turn."""
    return {
        "version": PLAN_VERSION,
        "status": READY if dependency_status in {"completed", "succeeded", "success"} else WAITING,
        "run_id": str(run.id),
        "surface": str(run.surface),
        "project_id": str(run.project_id) if run.project_id else None,
        "note_thread_id": str(run.note_thread_id) if run.note_thread_id else None,
        "action": str(action)[:80],
        "dependency_kind": str(dependency_kind)[:40],
        "dependency_id": str(dependency_id)[:128],
        "instruction": str(instruction or "").strip()[:2_000],
        "arguments": _safe_json(arguments or {}, max_chars=4_000),
        "dependency_status": str(dependency_status)[:40],
        "dependency_result": None,
        "attempts": 0,
        "created_at": _now(),
        "updated_at": _now(),
    }


def get_continuation_plan(run: Any) -> dict[str, Any] | None:
    metadata = getattr(run, "run_metadata", None) or {}
    plan = metadata.get(PLAN_KEY) if isinstance(metadata, dict) else None
    return dict(plan) if isinstance(plan, dict) else None


def attach_continuation_plan(run: Any, plan: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(getattr(run, "run_metadata", None) or {})
    metadata[PLAN_KEY] = _safe_json(plan, max_chars=12_000)
    run.run_metadata = metadata
    run.resumable = True
    return metadata[PLAN_KEY]


def mark_continuation_running(run: Any) -> dict[str, Any] | None:
    plan = get_continuation_plan(run)
    if not plan or plan.get("status") not in {READY, FAILED}:
        return None
    plan["status"] = RUNNING
    plan["attempts"] = int(plan.get("attempts") or 0) + 1
    plan["updated_at"] = _now()
    attach_continuation_plan(run, plan)
    return plan


def continuation_is_ready(run: Any) -> bool:
    plan = get_continuation_plan(run)
    return bool(plan and plan.get("status") == READY)


def continuation_can_resume(run: Any) -> bool:
    """Return whether a completed dependency has not been consumed yet."""
    plan = get_continuation_plan(run)
    return bool(plan and plan.get("status") in {READY, FAILED, RUNNING})


def mark_continuation_consumed(run: Any) -> dict[str, Any] | None:
    """Close a continuation after its resumed agent turn produced a result."""
    plan = get_continuation_plan(run)
    if not plan or plan.get("status") not in {READY, FAILED, RUNNING}:
        return None
    plan["status"] = DONE
    plan["updated_at"] = _now()
    attach_continuation_plan(run, plan)
    run.resumable = False
    return plan


def continuation_prompt(plan: dict[str, Any]) -> str:
    """Return a deterministic resume instruction; the model need not re-queue."""
    status = str(plan.get("dependency_status") or "completed")
    result = plan.get("dependency_result")
    result_text = json.dumps(result, default=str, sort_keys=True)[:2_000] if result else "{}"
    return (
        f"Continue the saved async request for {plan.get('action', 'the requested action')}. "
        f"Its {plan.get('dependency_kind', 'dependency')} {plan.get('dependency_id', '')} "
        f"finished with status {status}. Inspect the current state and report what changed, "
        "any failure details, and the next safe step. Do not enqueue the same action again "
        f"unless the current evidence requires it. Dependency result: {result_text}"
    )


def mark_dependency_complete(
    db: Any,
    *,
    dependency_kind: str,
    dependency_id: str,
    dependency_status: str,
    result: Any = None,
) -> int:
    """Advance matching plans after a job or note execution reaches a terminal state."""
    from app.models.runs import AgentRun
    from app.services.agent_runs import append_run_event

    normalized_status = str(dependency_status or "unknown").lower()
    successful = normalized_status in {"completed", "succeeded", "success"}
    target = READY if successful else FAILED
    changed = 0
    runs = (
        db.query(AgentRun)
        .filter(AgentRun.run_metadata.isnot(None))
        .all()
    )
    for run in runs:
        plan = get_continuation_plan(run)
        if not plan or plan.get("status") != WAITING:
            continue
        if str(plan.get("dependency_kind")) != str(dependency_kind):
            continue
        if str(plan.get("dependency_id")) != str(dependency_id):
            continue
        plan["status"] = target
        plan["dependency_status"] = normalized_status[:40]
        plan["dependency_result"] = _safe_json(result, max_chars=4_000)
        plan["updated_at"] = _now()
        attach_continuation_plan(run, plan)
        append_run_event(
            db,
            run,
            "continuation_ready" if successful else "continuation_failed",
            {
                "action": plan.get("action"),
                "dependency_kind": dependency_kind,
                "dependency_id": dependency_id,
                "status": normalized_status,
            },
            idempotency_key=f"continuation:{dependency_kind}:{dependency_id}:{normalized_status}",
        )
        changed += 1
    return changed


__all__ = [
    "FAILED",
    "DONE",
    "PLAN_KEY",
    "READY",
    "RUNNING",
    "WAITING",
    "attach_continuation_plan",
    "build_continuation_plan",
    "continuation_is_ready",
    "continuation_can_resume",
    "continuation_prompt",
    "get_continuation_plan",
    "mark_continuation_consumed",
    "mark_continuation_running",
    "mark_dependency_complete",
]
