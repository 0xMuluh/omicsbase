"""Shared durable run state, replay events, idempotency, and telemetry."""

from __future__ import annotations

import asyncio
import hashlib
import time
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.models.runs import AgentRun, RunEvent, RunTelemetry
from app.services.sanitizer import sanitize_text

MAX_EVENT_PAYLOAD_CHARS = 16_000
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}

# The database is the source of truth. This registry only prevents duplicate
# in-process workers and lets a reconnect distinguish an active worker from a
# stale run after a process restart.
_ACTIVE_RUN_TASKS: dict[str, asyncio.Task] = {}

RUN_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "paused", "cancel_requested", "cancelled", "failed"},
    "running": {"running", "waiting_tool", "paused", "cancel_requested", "completed", "failed", "cancelled"},
    "waiting_tool": {"running", "paused", "cancel_requested", "completed", "failed", "cancelled"},
    "paused": {"running", "cancel_requested", "cancelled", "failed"},
    "cancel_requested": {"cancelled", "failed", "completed"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
}


class IdempotencyConflict(ValueError):
    """The same key was reused with a different request payload."""


class RunTransitionError(ValueError):
    """A run attempted an invalid state transition."""


def session_factory_for(db: Session) -> Callable[[], Session]:
    """Create fresh sessions on the same bind as a request session."""
    bind = db.get_bind()
    return sessionmaker(autocommit=False, autoflush=False, bind=bind)


def register_run_task(run_id: str, task: asyncio.Task) -> None:
    """Register the current process worker for observability and dedupe."""
    _ACTIVE_RUN_TASKS[str(run_id)] = task


def unregister_run_task(run_id: str, task: asyncio.Task | None = None) -> None:
    """Remove a worker only if it is still the task that owns the run."""
    key = str(run_id)
    current = _ACTIVE_RUN_TASKS.get(key)
    if task is None or current is task:
        _ACTIVE_RUN_TASKS.pop(key, None)


def is_run_task_active(run_id: str) -> bool:
    """Return whether this process currently owns a live worker for a run."""
    task = _ACTIVE_RUN_TASKS.get(str(run_id))
    if task is None:
        return False
    if task.done():
        _ACTIVE_RUN_TASKS.pop(str(run_id), None)
        return False
    return True


def _now() -> datetime:
    return datetime.now(timezone.utc)


def request_fingerprint(payload: Any) -> str:
    """Hash a canonical request without storing the request body in the key."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def normalize_idempotency_key(value: str | None) -> str:
    key = " ".join(str(value or "").split()).strip()
    if not key:
        return str(uuid.uuid4())
    if len(key) <= 255:
        return key
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _safe_payload(payload: Any, *, max_chars: int = MAX_EVENT_PAYLOAD_CHARS) -> Any:
    """Make event/telemetry payloads JSON-safe, bounded, and secret-redacted."""
    try:
        encoded = json.dumps(payload if payload is not None else {}, default=str, sort_keys=True)
    except (TypeError, ValueError):
        encoded = json.dumps({"value": str(payload)[:max_chars]})
    encoded = sanitize_text(encoded)
    if len(encoded) > max_chars:
        return {
            "_truncated": True,
            "preview": encoded[:max_chars],
            "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        }
    try:
        return json.loads(encoded)
    except json.JSONDecodeError:
        return {"value": encoded[:max_chars]}


def create_or_get_agent_run(
    db: Session,
    *,
    tenant_id: str,
    owner_id: str,
    surface: str,
    idempotency_scope: str,
    idempotency_key: str | None,
    request_payload: Any,
    project_id: str | None = None,
    note_thread_id: str | None = None,
    kind: str = "agent_turn",
    run_metadata: dict[str, Any] | None = None,
) -> tuple[AgentRun, bool]:
    """Atomically reuse a turn or create its first durable run record."""
    if surface not in {"workspace", "notes"}:
        raise ValueError(f"Unsupported agent run surface: {surface}")
    key = normalize_idempotency_key(idempotency_key)
    fingerprint = request_fingerprint(request_payload)
    existing = (
        db.query(AgentRun)
        .filter(
            AgentRun.tenant_id == str(tenant_id),
            AgentRun.idempotency_scope == str(idempotency_scope),
            AgentRun.idempotency_key == key,
        )
        .with_for_update()
        .one_or_none()
    )
    if existing is not None:
        if existing.request_hash != fingerprint:
            raise IdempotencyConflict(
                "The idempotency key was already used for a different request."
            )
        return existing, False

    run = AgentRun(
        id=str(uuid.uuid4()),
        tenant_id=str(tenant_id),
        owner_id=str(owner_id),
        surface=surface,
        kind=kind,
        project_id=str(project_id) if project_id else None,
        note_thread_id=str(note_thread_id) if note_thread_id else None,
        status="queued",
        idempotency_scope=str(idempotency_scope)[:255],
        idempotency_key=key,
        request_hash=fingerprint,
        input_payload=_safe_payload(request_payload),
        run_metadata=_safe_payload(run_metadata or {}),
        heartbeat_at=_now(),
    )
    db.add(run)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(AgentRun)
            .filter(
                AgentRun.tenant_id == str(tenant_id),
                AgentRun.idempotency_scope == str(idempotency_scope),
                AgentRun.idempotency_key == key,
            )
            .with_for_update()
            .one_or_none()
        )
        if existing is None:
            raise
        if existing.request_hash != fingerprint:
            raise IdempotencyConflict(
                "The idempotency key was already used for a different request."
            )
        return existing, False
    append_run_event(
        db,
        run,
        "run_queued",
        {"surface": surface, "kind": kind},
    )
    return run, True


def lock_agent_run(db: Session, run: AgentRun) -> AgentRun:
    """Refresh a run under a row lock before mutating state or sequence."""
    with db.no_autoflush:
        found_id = (
            db.query(AgentRun.id)
            .filter(AgentRun.id == str(run.id))
            .with_for_update()
            .scalar()
        )
    if found_id is None:
        raise ValueError(f"Agent run {run.id} not found")
    db.refresh(run)
    return run


def append_run_event(
    db: Session,
    run: AgentRun,
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    idempotency_key: str | None = None,
) -> RunEvent:
    """Append one ordered event; repeated event keys return the original row."""
    db.flush()
    if idempotency_key:
        existing = (
            db.query(RunEvent)
            .filter(
                RunEvent.run_id == str(run.id),
                RunEvent.idempotency_key == str(idempotency_key),
            )
            .one_or_none()
        )
        if existing is not None:
            return existing

    with db.no_autoflush:
        current_sequence = (
            db.query(AgentRun.event_sequence)
            .filter(AgentRun.id == str(run.id))
            .with_for_update()
            .scalar()
        )
    if current_sequence is None:
        raise ValueError(f"Agent run {run.id} not found")
    sequence = max(int(current_sequence or 0), int(run.event_sequence or 0)) + 1
    run.event_sequence = sequence
    run.heartbeat_at = _now()
    run.updated_at = _now()
    event = RunEvent(
        id=str(uuid.uuid4()),
        run_id=str(run.id),
        sequence=sequence,
        event_type=str(event_type)[:64],
        status=str(run.status or "unknown")[:32],
        idempotency_key=str(idempotency_key)[:255] if idempotency_key else None,
        event_payload=_safe_payload(payload or {}),
    )
    db.add(event)
    return event


def transition_agent_run(
    db: Session,
    run: AgentRun,
    target_status: str,
    *,
    event_type: str | None = None,
    payload: dict[str, Any] | None = None,
    step: int | None = None,
) -> RunEvent | None:
    """Apply a validated state transition and append its lifecycle event."""
    target_status = str(target_status)
    current_status = str(run.status or "queued")
    if current_status == target_status:
        if event_type:
            return append_run_event(db, run, event_type, payload)
        return None
    if target_status not in RUN_TRANSITIONS.get(current_status, set()):
        raise RunTransitionError(f"Cannot transition run from {current_status} to {target_status}")

    run.status = target_status
    if step is not None:
        run.current_step = max(0, int(step))
    now = _now()
    run.heartbeat_at = now
    run.updated_at = now
    if target_status == "running" and run.started_at is None:
        run.started_at = now
    if target_status in TERMINAL_RUN_STATUSES:
        run.finished_at = now
    return append_run_event(
        db,
        run,
        event_type or f"run_{target_status}",
        payload,
    )


def record_stream_event(
    db: Session,
    run: AgentRun,
    event: dict[str, Any],
) -> RunEvent:
    """Persist a bounded public stream milestone for replay."""
    event_type = str(event.get("type") or "stream_event")
    idempotency_key = None
    tool_call_id = event.get("tool_call_id") or event.get("id")
    if event_type in {"tool_started", "tool_completed", "execution_queued"} and tool_call_id:
        idempotency_key = f"{event_type}:{tool_call_id}"
    if event_type in {"message", "note_cell"}:
        message_id = event.get("message_id")
        if not message_id and isinstance(event.get("cell"), dict):
            message_id = event["cell"].get("id")
        if message_id:
            idempotency_key = f"input:{message_id}"
    if event_type in {"tool_started", "execution_queued", "action_queued", "action_event"}:
        run.resumable = False
    if event_type == "note_cell" and event.get("role") != "user":
        run.resumable = False
    return append_run_event(
        db,
        run,
        event_type,
        {key: value for key, value in event.items() if event_type != "token_chunk" or key != "token"},
        idempotency_key=idempotency_key,
    )


def telemetry_from_usage(
    usage: Any,
    *,
    fallback_input_tokens: int,
    fallback_output_tokens: int,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return telemetry fields while marking estimates and optional pricing."""
    values = usage if isinstance(usage, dict) else {}
    input_tokens = values.get("input_tokens")
    output_tokens = values.get("output_tokens")
    try:
        input_tokens = int(input_tokens) if input_tokens is not None else int(fallback_input_tokens)
    except (TypeError, ValueError):
        input_tokens = int(fallback_input_tokens)
    try:
        output_tokens = int(output_tokens) if output_tokens is not None else int(fallback_output_tokens)
    except (TypeError, ValueError):
        output_tokens = int(fallback_output_tokens)
    exact = values.get("input_tokens") is not None and values.get("output_tokens") is not None
    result_metadata = dict(metadata or {})
    result_metadata["estimated_tokens"] = not exact
    if values:
        result_metadata["provider_usage"] = _safe_payload(values)
    from app.config import settings
    input_rate = float(getattr(settings, "llm_input_cost_per_million", 0) or 0)
    output_rate = float(getattr(settings, "llm_output_cost_per_million", 0) or 0)
    cost_usd = None
    if input_rate or output_rate:
        cost_usd = (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
        "metadata": result_metadata,
    }


def record_run_telemetry(
    db: Session,
    run: AgentRun,
    *,
    kind: str,
    operation: str,
    status: str = "completed",
    duration_ms: float | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
    cost_usd: float | None = None,
    provider: str | None = None,
    model: str | None = None,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> RunTelemetry:
    """Persist one bounded telemetry sample for a run."""
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = int(input_tokens) + int(output_tokens)
    finished_at = _now()
    started_at = finished_at - timedelta(milliseconds=float(duration_ms)) if duration_ms is not None else None
    sample = RunTelemetry(
        id=str(uuid.uuid4()),
        run_id=str(run.id),
        kind=str(kind)[:32],
        operation=str(operation)[:128],
        provider=str(provider)[:64] if provider else None,
        model=str(model)[:255] if model else None,
        status=str(status)[:32],
        duration_ms=float(duration_ms) if duration_ms is not None else None,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cost_usd=float(cost_usd) if cost_usd is not None else None,
        error=sanitize_text(str(error))[:4000] if error else None,
        telemetry_metadata=_safe_payload(metadata or {}),
        started_at=started_at,
        finished_at=finished_at,
    )
    db.add(sample)
    return sample


def approximate_tokens(value: Any) -> int:
    """Cheap provider-neutral token estimate used when streaming usage is absent."""
    if value is None:
        return 0
    return max(0, (len(str(value)) + 3) // 4)


def request_run_cancel(db: Session, run: AgentRun) -> AgentRun:
    """Make cancellation durable and idempotent."""
    run = lock_agent_run(db, run)
    if run.status in TERMINAL_RUN_STATUSES:
        return run
    run.cancel_requested = True
    if run.status == "queued":
        transition_agent_run(
            db,
            run,
            "cancelled",
            event_type="run_cancelled",
            payload={"reason": "cancelled before execution"},
        )
    elif run.status != "cancel_requested":
        transition_agent_run(
            db,
            run,
            "cancel_requested",
            event_type="run_cancel_requested",
        )
    else:
        append_run_event(db, run, "run_cancel_requested", {"idempotent": True})
    return run


def run_cancel_requested(db: Session, run_id: str) -> bool:
    value = (
        db.query(AgentRun.cancel_requested)
        .filter(AgentRun.id == str(run_id))
        .scalar()
    )
    return bool(value)


def get_agent_run(db: Session, run_id: str, tenant_id: str) -> AgentRun | None:
    return (
        db.query(AgentRun)
        .filter(AgentRun.id == str(run_id), AgentRun.tenant_id == str(tenant_id))
        .one_or_none()
    )


def serialize_run_telemetry(telemetry: RunTelemetry) -> dict[str, Any]:
    return {
        "id": str(telemetry.id),
        "run_id": str(telemetry.run_id),
        "kind": telemetry.kind,
        "operation": telemetry.operation,
        "provider": telemetry.provider,
        "model": telemetry.model,
        "status": telemetry.status,
        "duration_ms": telemetry.duration_ms,
        "input_tokens": telemetry.input_tokens,
        "output_tokens": telemetry.output_tokens,
        "total_tokens": telemetry.total_tokens,
        "cost_usd": telemetry.cost_usd,
        "error": telemetry.error,
        "metadata": telemetry.telemetry_metadata or {},
        "started_at": telemetry.started_at,
        "finished_at": telemetry.finished_at,
        "created_at": telemetry.created_at,
    }


def list_run_telemetry(db: Session, run_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
    rows = (
        db.query(RunTelemetry)
        .filter(RunTelemetry.run_id == str(run_id))
        .order_by(RunTelemetry.created_at.asc())
        .limit(max(1, min(int(limit), 2000)))
        .all()
    )
    return [serialize_run_telemetry(row) for row in rows]


def pause_stale_agent_runs(db: Session, *, stale_after_seconds: float = 300) -> int:
    """Mark orphaned non-terminal workers paused during process startup."""
    cutoff = _now() - timedelta(seconds=max(1, float(stale_after_seconds)))
    runs = (
        db.query(AgentRun)
        .filter(
            AgentRun.status.in_({"queued", "running", "waiting_tool", "cancel_requested"}),
            or_(AgentRun.heartbeat_at.is_(None), AgentRun.heartbeat_at < cutoff),
        )
        .with_for_update()
        .all()
    )
    changed = 0
    for run in runs:
        target = "cancelled" if run.cancel_requested else "paused"
        if run.status == target:
            continue
        transition_agent_run(
            db,
            run,
            target,
            event_type="run_cancelled" if target == "cancelled" else "run_paused",
            payload={"reason": "stale worker detected during startup"},
        )
        changed += 1
    if changed:
        db.commit()
    return changed


def serialize_agent_run(run: AgentRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "tenant_id": str(run.tenant_id),
        "owner_id": str(run.owner_id),
        "surface": run.surface,
        "kind": run.kind,
        "project_id": str(run.project_id) if run.project_id else None,
        "note_thread_id": str(run.note_thread_id) if run.note_thread_id else None,
        "status": run.status,
        "idempotency_scope": run.idempotency_scope,
        "idempotency_key": run.idempotency_key,
        "current_step": run.current_step,
        "event_sequence": run.event_sequence,
        "cancel_requested": bool(run.cancel_requested),
        "resumable": bool(run.resumable),
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "heartbeat_at": run.heartbeat_at,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "result": run.result_payload,
        "metadata": run.run_metadata,
    }


def serialize_run_event(event: RunEvent) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "run_id": str(event.run_id),
        "sequence": event.sequence,
        "event_type": event.event_type,
        "status": event.status,
        "payload": event.event_payload or {},
        "created_at": event.created_at,
    }


def list_run_events(
    db: Session,
    run_id: str,
    *,
    after_sequence: int = 0,
    limit: int = 500,
) -> list[dict[str, Any]]:
    events = (
        db.query(RunEvent)
        .filter(
            RunEvent.run_id == str(run_id),
            RunEvent.sequence > max(0, int(after_sequence)),
        )
        .order_by(RunEvent.sequence.asc())
        .limit(max(1, min(int(limit), 2000)))
        .all()
    )
    return [serialize_run_event(event) for event in events]


async def replay_agent_run_stream(
    run_id: str,
    tenant_id: str,
    *,
    max_wait_seconds: float = 3600,
    session_factory: Callable[[], Session] | None = None,
):
    """Replay durable events and poll until a live run reaches a terminal state."""
    if session_factory is None:
        from app.database import SessionLocal

        session_factory = SessionLocal

    cursor = 0
    emitted_run = False
    deadline = time.monotonic() + max_wait_seconds
    while time.monotonic() < deadline:
        db = session_factory()
        try:
            run = get_agent_run(db, run_id, tenant_id)
            if run is None:
                return
            events = list_run_events(db, run_id, after_sequence=cursor)
            status = str(run.status)
        finally:
            db.close()
        if not emitted_run:
            yield {"type": "run", "run": serialize_agent_run(run), "run_id": run_id, "sequence": 0}
            emitted_run = True
        for event in events:
            cursor = max(cursor, int(event.get("sequence") or 0))
            event_name = str(event.get("event_type") or "")
            if event_name.startswith("run_") or event_name in {"tool_waiting", "tool_resumed"}:
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if event.get("event_type") == "token_chunk":
                replay = {"type": "token", "token": payload.get("token") or ""}
            else:
                replay = dict(payload) if payload.get("type") else {"type": event.get("event_type")}
            replay["run_id"] = run_id
            replay["sequence"] = cursor
            yield replay
        if status in TERMINAL_RUN_STATUSES:
            return
        if status == "paused":
            yield {
                "type": "paused",
                "status": "paused",
                "message": "This run was paused after the server lost its worker. Retry the same request to resume when it is safe, or continue with a new turn.",
                "run_id": run_id,
                "sequence": cursor,
            }
            return
        await asyncio.sleep(0.25)


__all__ = [
    "TERMINAL_RUN_STATUSES",
    "RUN_TRANSITIONS",
    "IdempotencyConflict",
    "RunTransitionError",
    "create_or_get_agent_run",
    "append_run_event",
    "transition_agent_run",
    "record_stream_event",
    "record_run_telemetry",
    "telemetry_from_usage",
    "approximate_tokens",
    "request_run_cancel",
    "run_cancel_requested",
    "get_agent_run",
    "serialize_agent_run",
    "pause_stale_agent_runs",
    "list_run_events",
    "serialize_run_telemetry",
    "list_run_telemetry",
    "replay_agent_run_stream",
    "session_factory_for",
    "register_run_task",
    "unregister_run_task",
    "is_run_task_active",
    "normalize_idempotency_key",
    "request_fingerprint",
]

