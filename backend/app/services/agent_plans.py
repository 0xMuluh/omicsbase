"""Durable continuation plans for asynchronous agent actions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.config import settings

PLAN_KEY = "continuation_plan"
PLAN_VERSION = 1
WAITING = "waiting"
READY = "ready"
RUNNING = "running"
FAILED = "failed"
DONE = "done"
DEAD_LETTER = "dead_letter"
DEFAULT_MAX_CONTINUATION_ATTEMPTS = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _max_continuation_attempts() -> int:
    try:
        configured = int(getattr(settings, "agent_continuation_max_attempts", DEFAULT_MAX_CONTINUATION_ATTEMPTS) or DEFAULT_MAX_CONTINUATION_ATTEMPTS)
    except (TypeError, ValueError):
        configured = DEFAULT_MAX_CONTINUATION_ATTEMPTS
    return max(1, min(configured, 8))


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
        "goal": str(instruction or "").strip()[:2_000],
        "arguments": _safe_json(arguments or {}, max_chars=4_000),
        "dependency_status": str(dependency_status)[:40],
        "dependency_result": None,
        "attempts": 0,
        "max_attempts": _max_continuation_attempts(),
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
    if hasattr(run, "continuation_status"):
        run.continuation_status = str(plan.get("status") or "")[:32] or None
        run.continuation_dependency_kind = str(plan.get("dependency_kind") or "")[:40] or None
        run.continuation_dependency_id = str(plan.get("dependency_id") or "")[:128] or None
        run.continuation_attempts = int(plan.get("attempts") or 0)
    run.resumable = plan.get("status") not in {DONE, DEAD_LETTER}
    return metadata[PLAN_KEY]


def mark_continuation_running(run: Any) -> dict[str, Any] | None:
    plan = get_continuation_plan(run)
    if not plan or plan.get("status") not in {READY, FAILED}:
        return None
    attempts = int(plan.get("attempts") or 0)
    max_attempts = int(plan.get("max_attempts") or _max_continuation_attempts())
    if attempts >= max_attempts:
        plan["status"] = DEAD_LETTER
        plan["max_attempts"] = max_attempts
        plan["requires_user"] = True
        plan["failure_reason"] = "Continuation attempt ceiling reached."
        plan["updated_at"] = _now()
        attach_continuation_plan(run, plan)
        return None
    plan["status"] = RUNNING
    plan["attempts"] = attempts + 1
    plan["max_attempts"] = max_attempts
    plan["updated_at"] = _now()
    attach_continuation_plan(run, plan)
    return plan

def continuation_is_ready(run: Any) -> bool:
    plan = get_continuation_plan(run)
    return bool(plan and plan.get("status") == READY)


def continuation_can_resume(run: Any) -> bool:
    """Return whether a completed dependency has not been consumed yet."""
    plan = get_continuation_plan(run)
    if not plan or plan.get("status") not in {READY, FAILED, RUNNING}:
        return False
    attempts = int(plan.get("attempts") or 0)
    max_attempts = int(plan.get("max_attempts") or _max_continuation_attempts())
    return attempts < max_attempts or plan.get("status") == RUNNING


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
    """Return a deterministic resume instruction with the unresolved goal."""
    status = str(plan.get("dependency_status") or "completed")
    result = plan.get("dependency_result")
    result_text = json.dumps(result, default=str, sort_keys=True)[:2_000] if result else "{}"
    goal = str(plan.get("goal") or plan.get("instruction") or "").strip()[:2_000]
    arguments = plan.get("arguments") if isinstance(plan.get("arguments"), dict) else {}
    arguments_text = json.dumps(arguments, default=str, sort_keys=True)[:4_000]
    return (
        f"Resume the saved async request for {plan.get('action', 'the requested action')}. "
        f"Original unresolved goal: {goal or 'Complete the user request safely.'} "
        f"Saved action arguments: {arguments_text}. "
        f"Its {plan.get('dependency_kind', 'dependency')} {plan.get('dependency_id', '')} "
        f"finished with status {status}. Inspect the current state and continue toward the goal; "
        "report what changed, any failure details, and the next safe step. Do not enqueue the "
        "same action again unless the current evidence requires it. "
        f"Dependency result: {result_text}"
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
        .filter(
            AgentRun.continuation_status == WAITING,
            AgentRun.continuation_dependency_kind == str(dependency_kind),
            AgentRun.continuation_dependency_id == str(dependency_id),
        )
        .with_for_update()
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
    "DEAD_LETTER",
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
