"""Durable continuation plans for asynchronous agent actions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.config import settings

PLAN_KEY = "continuation_plan"
PLAN_VERSION = 2
MAX_PLAN_STEPS = 8
WAITING = "waiting"
READY = "ready"
RUNNING = "running"
FAILED = "failed"
DONE = "done"
DEAD_LETTER = "dead_letter"
ACTIVE_STEP_STATUSES = {WAITING, READY, RUNNING, FAILED}
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


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _is_success_status(status: Any) -> bool:
    return str(status or "").lower() in {"completed", "succeeded", "success"}


def _is_failure_status(status: Any) -> bool:
    return str(status or "").lower() in {
        "failed",
        "error",
        "cancelled",
        "canceled",
        "dead_letter",
    }


def _bounded_depends_on(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for item in value:
        item_id = str(item or "").strip()[:64]
        if item_id and item_id not in result:
            result.append(item_id)
        if len(result) >= MAX_PLAN_STEPS:
            break
    return result


def _bounded_arguments(value: Any, *, max_chars: int = 1_600) -> dict[str, Any]:
    bounded = _safe_json(value if isinstance(value, dict) else {}, max_chars=max_chars)
    return bounded if isinstance(bounded, dict) else {}


def _new_step(
    *,
    step_id: str,
    action: str,
    dependency_kind: str,
    dependency_id: str,
    instruction: str,
    arguments: dict[str, Any] | None = None,
    dependency_status: str = "queued",
    dependency_result: Any = None,
    depends_on: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    dependency_state = str(dependency_status or "completed").lower()[:40]
    internal_dependencies = _bounded_depends_on(depends_on)
    status = READY if _is_success_status(dependency_state) and not internal_dependencies else WAITING
    return {
        "id": str(step_id)[:64],
        "action": str(action)[:80],
        "dependency_kind": str(dependency_kind)[:40],
        "dependency_id": str(dependency_id)[:128],
        "instruction": str(instruction or "").strip()[:1_200],
        "goal": str(instruction or "").strip()[:1_200],
        "arguments": _bounded_arguments(arguments),
        "dependency_status": dependency_state,
        "dependency_result": (
            _safe_json(dependency_result, max_chars=1_800)
            if dependency_result is not None
            else None
        ),
        "depends_on": internal_dependencies,
        "status": status,
        "attempts": 0,
        "max_attempts": _max_continuation_attempts(),
        "created_at": _now(),
        "updated_at": _now(),
    }


def _coerce_step(
    raw: Any,
    index: int,
    plan: dict[str, Any],
    used_ids: set[str],
) -> dict[str, Any]:
    source = dict(raw) if isinstance(raw, dict) else {}
    candidate_id = str(source.get("id") or f"step-{index}")[:64] or f"step-{index}"
    if candidate_id in used_ids:
        candidate_id = f"step-{index}"
    used_ids.add(candidate_id)

    dependency_kind = source.get(
        "dependency_kind",
        plan.get("dependency_kind") if index == 1 else "",
    )
    dependency_id = source.get(
        "dependency_id",
        plan.get("dependency_id") if index == 1 else "",
    )
    dependency_status = source.get(
        "dependency_status",
        plan.get("dependency_status") if index == 1 else "completed",
    )
    instruction = source.get(
        "instruction",
        plan.get("instruction") if index == 1 else plan.get("goal", ""),
    )
    arguments = source.get(
        "arguments",
        plan.get("arguments") if index == 1 else {},
    )
    step = _new_step(
        step_id=candidate_id,
        action=str(source.get("action") or plan.get("action") or "continuation"),
        dependency_kind=str(dependency_kind or ""),
        dependency_id=str(dependency_id or ""),
        instruction=str(source.get("goal") or instruction or ""),
        arguments=arguments if isinstance(arguments, dict) else {},
        dependency_status=str(dependency_status or "completed"),
        dependency_result=source.get("dependency_result"),
        depends_on=_bounded_depends_on(source.get("depends_on")),
    )
    explicit_status = str(source.get("status") or "").lower()
    if explicit_status in {WAITING, READY, RUNNING, FAILED, DONE, DEAD_LETTER}:
        step["status"] = explicit_status
    step["attempts"] = max(0, _safe_int(source.get("attempts"), 0))
    step["max_attempts"] = max(
        1,
        min(_safe_int(source.get("max_attempts"), _max_continuation_attempts()), 8),
    )
    for key in ("created_at", "updated_at", "completed_at", "failure_reason", "requires_user"):
        if key in source:
            step[key] = source[key]
    return step


def _refresh_step(step: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> None:
    status = str(step.get("status") or WAITING)
    if status in {DONE, DEAD_LETTER, RUNNING}:
        return
    dependencies_done = all(
        dependency_id in by_id and by_id[dependency_id].get("status") == DONE
        for dependency_id in step.get("depends_on", [])
    )
    dependency_status = str(step.get("dependency_status") or "completed").lower()
    if _is_failure_status(dependency_status):
        step["status"] = FAILED
    elif status == FAILED and _is_success_status(dependency_status) and dependencies_done:
        step["status"] = FAILED
    elif _is_success_status(dependency_status) and dependencies_done:
        step["status"] = READY
    else:
        step["status"] = WAITING


def _set_envelope_from_step(plan: dict[str, Any], step: dict[str, Any]) -> None:
    plan.update(
        {
            "status": step.get("status") or WAITING,
            "action": step.get("action"),
            "dependency_kind": step.get("dependency_kind"),
            "dependency_id": step.get("dependency_id"),
            "instruction": step.get("instruction"),
            "goal": step.get("goal") or step.get("instruction"),
            "arguments": step.get("arguments") or {},
            "dependency_status": step.get("dependency_status"),
            "dependency_result": step.get("dependency_result"),
            "attempts": step.get("attempts", 0),
            "max_attempts": step.get("max_attempts", _max_continuation_attempts()),
        }
    )
    for key in ("requires_user", "failure_reason"):
        if key in step:
            plan[key] = step[key]


def _normalize_plan(plan: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(plan)
    raw_steps = normalized.get("steps")
    used_ids: set[str] = set()
    if isinstance(raw_steps, list) and raw_steps:
        step_sources = raw_steps[:MAX_PLAN_STEPS]
    else:
        step_sources = [
            {
                "id": "step-1",
                "action": normalized.get("action"),
                "dependency_kind": normalized.get("dependency_kind"),
                "dependency_id": normalized.get("dependency_id"),
                "instruction": normalized.get("instruction") or normalized.get("goal"),
                "arguments": normalized.get("arguments"),
                "dependency_status": normalized.get("dependency_status") or "queued",
                "dependency_result": normalized.get("dependency_result"),
                "status": normalized.get("status"),
                "attempts": normalized.get("attempts", 0),
                "max_attempts": normalized.get("max_attempts"),
            }
        ]
    steps = [
        _coerce_step(source, index, normalized, used_ids)
        for index, source in enumerate(step_sources, start=1)
    ]

    active_hint = str(normalized.get("active_step_id") or "")
    top_status = str(normalized.get("status") or "").lower()
    if active_hint and top_status in ACTIVE_STEP_STATUSES | {DONE, DEAD_LETTER}:
        for source, step in zip(step_sources, steps):
            if step["id"] == active_hint:
                if top_status != str(source.get("status") or "").lower():
                    step["status"] = top_status
                if "attempts" in normalized and _safe_int(normalized.get("attempts"), step["attempts"]) != step["attempts"]:
                    step["attempts"] = max(0, _safe_int(normalized.get("attempts"), step["attempts"]))
                if "max_attempts" in normalized and _safe_int(normalized.get("max_attempts"), step["max_attempts"]) != step["max_attempts"]:
                    step["max_attempts"] = max(
                        1,
                        min(_safe_int(normalized.get("max_attempts"), step["max_attempts"]), 8),
                    )
                break

    by_id = {step["id"]: step for step in steps}
    for step in steps:
        _refresh_step(step, by_id)

    active: dict[str, Any] | None = None
    if active_hint in by_id and by_id[active_hint].get("status") in ACTIVE_STEP_STATUSES:
        active = by_id[active_hint]
    if active is None:
        for candidate_status in (READY, FAILED, WAITING):
            active = next(
                (step for step in steps if step.get("status") == candidate_status),
                None,
            )
            if active is not None:
                break
    if active is None:
        active = next((step for step in reversed(steps) if step.get("status") == DEAD_LETTER), None)
    if active is None and steps:
        active = steps[-1]

    normalized["steps"] = steps
    normalized["step_count"] = len(steps)
    normalized["active_step_id"] = active.get("id") if active else None
    normalized["completed_steps"] = [
        step["id"] for step in steps if step.get("status") == DONE
    ]
    normalized["remaining_steps"] = [
        step["id"]
        for step in steps
        if step.get("status") not in {DONE, DEAD_LETTER}
    ]
    if active is not None:
        _set_envelope_from_step(normalized, active)
        normalized["current_step"] = next(
            (index for index, step in enumerate(steps, start=1) if step["id"] == active["id"]),
            len(steps),
        )
    elif steps:
        normalized["status"] = DONE if all(step.get("status") == DONE for step in steps) else DEAD_LETTER
        normalized["current_step"] = len(steps)
    else:
        normalized["status"] = DONE
        normalized["current_step"] = 0
    normalized["version"] = PLAN_VERSION
    return normalized


def build_continuation_plan(
    run: Any,
    *,
    action: str,
    dependency_kind: str,
    dependency_id: str,
    instruction: str,
    arguments: dict[str, Any] | None = None,
    dependency_status: str = "queued",
    steps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a bounded continuation graph for an asynchronous agent action."""
    plan = {
        "version": PLAN_VERSION,
        "run_id": str(run.id),
        "surface": str(run.surface),
        "project_id": str(run.project_id) if run.project_id else None,
        "note_thread_id": str(run.note_thread_id) if run.note_thread_id else None,
        "action": str(action)[:80],
        "dependency_kind": str(dependency_kind)[:40],
        "dependency_id": str(dependency_id)[:128],
        "instruction": str(instruction or "").strip()[:2_000],
        "goal": str(instruction or "").strip()[:2_000],
        "arguments": _bounded_arguments(arguments, max_chars=2_400),
        "dependency_status": str(dependency_status)[:40],
        "dependency_result": None,
        "attempts": 0,
        "max_attempts": _max_continuation_attempts(),
        "created_at": _now(),
        "updated_at": _now(),
    }
    if steps is None:
        plan["steps"] = [
            {
                "id": "step-1",
                "action": action,
                "dependency_kind": dependency_kind,
                "dependency_id": dependency_id,
                "instruction": instruction,
                "arguments": arguments or {},
                "dependency_status": dependency_status,
                "depends_on": [],
            }
        ]
    else:
        plan["steps"] = [dict(step) for step in steps[:MAX_PLAN_STEPS] if isinstance(step, dict)]
    return _normalize_plan(plan)


def get_continuation_plan(run: Any) -> dict[str, Any] | None:
    metadata = getattr(run, "run_metadata", None) or {}
    plan = metadata.get(PLAN_KEY) if isinstance(metadata, dict) else None
    return _normalize_plan(plan) if isinstance(plan, dict) else None


def attach_continuation_plan(run: Any, plan: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_plan(plan)
    metadata = dict(getattr(run, "run_metadata", None) or {})
    metadata[PLAN_KEY] = _safe_json(normalized, max_chars=24_000)
    run.run_metadata = metadata
    if hasattr(run, "continuation_status"):
        run.continuation_status = str(normalized.get("status") or "")[:32] or None
        run.continuation_dependency_kind = str(normalized.get("dependency_kind") or "")[:40] or None
        run.continuation_dependency_id = str(normalized.get("dependency_id") or "")[:128] or None
        run.continuation_attempts = max(0, _safe_int(normalized.get("attempts"), 0))
    if hasattr(run, "current_step"):
        run.current_step = max(0, _safe_int(normalized.get("current_step"), 0))
    run.resumable = normalized.get("status") not in {DONE, DEAD_LETTER}
    return metadata[PLAN_KEY]


def _active_step(plan: dict[str, Any]) -> dict[str, Any] | None:
    active_id = str(plan.get("active_step_id") or "")
    return next(
        (step for step in plan.get("steps", []) if step.get("id") == active_id),
        None,
    )


def append_continuation_step(
    run: Any,
    *,
    action: str,
    dependency_kind: str,
    dependency_id: str,
    instruction: str,
    arguments: dict[str, Any] | None = None,
    dependency_status: str = "queued",
    depends_on: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Append one dependent action while retaining prior graph progress."""
    plan = get_continuation_plan(run)
    if plan is None:
        plan = build_continuation_plan(
            run,
            action=action,
            dependency_kind=dependency_kind,
            dependency_id=dependency_id,
            instruction=instruction,
            arguments=arguments,
            dependency_status=dependency_status,
        )
        attach_continuation_plan(run, plan)
        return plan

    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    if len(steps) >= MAX_PLAN_STEPS:
        plan["status"] = DEAD_LETTER
        plan["requires_user"] = True
        plan["failure_reason"] = "Continuation graph step ceiling reached."
        attach_continuation_plan(run, plan)
        raise ValueError("Continuation graph step ceiling reached")

    active = _active_step(plan)
    if active is not None and active.get("status") == WAITING:
        raise ValueError("Cannot append a continuation while its dependency is still pending")
    if active is not None and active.get("status") == DEAD_LETTER:
        raise ValueError("Cannot append a continuation after a dead-lettered step")

    previous_id = str(active.get("id")) if active is not None else None
    if active is not None and active.get("status") in {READY, FAILED, RUNNING}:
        active["status"] = DONE
        active["completed_at"] = _now()
        active["updated_at"] = _now()
    if depends_on is None and previous_id:
        depends_on = [previous_id]

    new_id = f"step-{len(steps) + 1}"
    new_step = _new_step(
        step_id=new_id,
        action=action,
        dependency_kind=dependency_kind,
        dependency_id=dependency_id,
        instruction=instruction,
        arguments=arguments,
        dependency_status=dependency_status,
        depends_on=list(depends_on or []),
    )
    steps.append(new_step)
    plan["steps"] = steps
    plan["active_step_id"] = new_id
    _set_envelope_from_step(plan, new_step)
    plan["updated_at"] = _now()
    result = _normalize_plan(plan)
    attach_continuation_plan(run, result)
    return result


def mark_continuation_running(run: Any) -> dict[str, Any] | None:
    plan = get_continuation_plan(run)
    if not plan or plan.get("status") not in {READY, FAILED}:
        return None
    active = _active_step(plan)
    if active is None:
        return None
    attempts = max(0, _safe_int(active.get("attempts"), 0))
    max_attempts = max(
        1,
        min(_safe_int(active.get("max_attempts"), _max_continuation_attempts()), 8),
    )
    if attempts >= max_attempts:
        active["status"] = DEAD_LETTER
        active["max_attempts"] = max_attempts
        active["requires_user"] = True
        active["failure_reason"] = "Continuation attempt ceiling reached."
        plan["status"] = DEAD_LETTER
        plan["updated_at"] = _now()
        attach_continuation_plan(run, plan)
        return None
    active["status"] = RUNNING
    active["attempts"] = attempts + 1
    active["max_attempts"] = max_attempts
    active["updated_at"] = _now()
    _set_envelope_from_step(plan, active)
    plan["updated_at"] = _now()
    attach_continuation_plan(run, plan)
    return get_continuation_plan(run)


def continuation_is_ready(run: Any) -> bool:
    plan = get_continuation_plan(run)
    return bool(plan and plan.get("status") == READY)


def continuation_can_resume(run: Any) -> bool:
    """Return whether a ready graph step has not been consumed yet."""
    plan = get_continuation_plan(run)
    if not plan or plan.get("status") not in {READY, FAILED, RUNNING}:
        return False
    active = _active_step(plan)
    if active is None:
        return False
    attempts = max(0, _safe_int(active.get("attempts"), 0))
    max_attempts = max(
        1,
        min(_safe_int(active.get("max_attempts"), _max_continuation_attempts()), 8),
    )
    return attempts < max_attempts or plan.get("status") == RUNNING


def mark_continuation_consumed(run: Any) -> dict[str, Any] | None:
    """Complete the active graph step and activate its next ready node."""
    plan = get_continuation_plan(run)
    if not plan or plan.get("status") not in {READY, FAILED, RUNNING}:
        return None
    active = _active_step(plan)
    if active is None:
        return None
    active["status"] = DONE
    active["completed_at"] = _now()
    active["updated_at"] = _now()
    plan["status"] = DONE
    plan["active_step_id"] = active["id"]
    plan["updated_at"] = _now()
    normalized = _normalize_plan(plan)
    attach_continuation_plan(run, normalized)
    return get_continuation_plan(run)


def continuation_prompt(plan: dict[str, Any]) -> str:
    """Return a deterministic resume instruction with graph progress."""
    normalized = _normalize_plan(plan)
    active = _active_step(normalized)
    current_step = _safe_int(normalized.get("current_step"), 1)
    step_count = _safe_int(normalized.get("step_count"), len(normalized.get("steps", [])))
    result = normalized.get("dependency_result")
    result_text = json.dumps(result, default=str, sort_keys=True)[:2_000] if result else "{}"
    goal = str(normalized.get("goal") or normalized.get("instruction") or "").strip()[:2_000]
    arguments = normalized.get("arguments") if isinstance(normalized.get("arguments"), dict) else {}
    arguments_text = json.dumps(arguments, default=str, sort_keys=True)[:4_000]
    completed = ", ".join(normalized.get("completed_steps") or []) or "none"
    remaining_goals = [
        str(step.get("goal") or step.get("instruction") or "").strip()[:300]
        for step in normalized.get("steps", [])
        if step.get("status") not in {DONE, DEAD_LETTER}
    ]
    active_action = active.get("action") if active else normalized.get("action", "the requested action")
    return (
        f"Resume the saved async request for {active_action}. "
        f"Step {current_step} of {step_count}. Completed steps: {completed}. "
        f"Remaining graph goals: {json.dumps(remaining_goals, default=str)[:1_500]}. "
        f"Original unresolved goal: {goal or "Complete the user request safely."} "
        f"Saved action arguments: {arguments_text}. "
        f"Its {normalized.get("dependency_kind", "dependency")} {normalized.get("dependency_id", "")} "
        f"finished with status {normalized.get("dependency_status", "completed")}. "
        "Inspect the current state and continue toward the goal; report what changed, any "
        "failure details, and the next safe step. Do not enqueue the same action again unless "
        f"the current evidence requires it. Dependency result: {result_text}"
    )


def mark_dependency_complete(
    db: Any,
    *,
    dependency_kind: str,
    dependency_id: str,
    dependency_status: str,
    result: Any = None,
) -> int:
    """Advance the indexed active node after its external dependency completes."""
    from app.models.runs import AgentRun
    from app.services.agent_runs import append_run_event

    normalized_status = str(dependency_status or "unknown").lower()
    successful = _is_success_status(normalized_status)
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
        active = _active_step(plan)
        if active is None:
            continue
        if str(active.get("dependency_kind")) != str(dependency_kind):
            continue
        if str(active.get("dependency_id")) != str(dependency_id):
            continue
        if str(active.get("dependency_status") or "").lower() == normalized_status:
            continue
        active["dependency_status"] = normalized_status[:40]
        active["dependency_result"] = _safe_json(result, max_chars=1_800)
        active["status"] = target
        active["updated_at"] = _now()
        _set_envelope_from_step(plan, active)
        plan["updated_at"] = _now()
        normalized = _normalize_plan(plan)
        attach_continuation_plan(run, normalized)
        append_run_event(
            db,
            run,
            (
                "continuation_ready"
                if normalized.get("status") == READY
                else "continuation_failed"
                if not successful
                else "continuation_waiting"
            ),
            {
                "action": active.get("action"),
                "step_id": active.get("id"),
                "dependency_kind": dependency_kind,
                "dependency_id": dependency_id,
                "status": normalized_status,
            },
            idempotency_key=f"continuation:{active.get("id")}:{dependency_kind}:{dependency_id}:{normalized_status}",
        )
        changed += 1
    return changed


__all__ = [
    "ACTIVE_STEP_STATUSES",
    "DEAD_LETTER",
    "DONE",
    "FAILED",
    "MAX_PLAN_STEPS",
    "PLAN_KEY",
    "READY",
    "RUNNING",
    "WAITING",
    "append_continuation_step",
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
