"""Shared durable run state, replay events, idempotency, and telemetry."""

from __future__ import annotations

import asyncio
import hashlib
import time
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.models.runs import AgentRun, RunEvent, RunTelemetry
from app.services.sanitizer import sanitize_text

MAX_EVENT_PAYLOAD_CHARS = 16_000
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}
RUN_TO_JOB_STATUS = {
    "queued": "pending",
    "running": "running",
    "waiting_tool": "running",
    "paused": "paused",
    "cancel_requested": "cancel_requested",
    "completed": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
}

# Live token chunks are flushed to the client and to the event stream once
# they accumulate this many characters, so streaming feels incremental.
TOKEN_CHUNK_FLUSH_CHARS = 96

# The database is the source of truth. This registry only prevents duplicate
# in-process workers and lets a reconnect distinguish an active worker from a
# stale run after a process restart.
_ACTIVE_RUN_TASKS: dict[str, asyncio.Task] = {}

RUN_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "paused", "cancel_requested", "cancelled", "failed"},
    "running": {"running", "waiting_tool", "paused", "cancel_requested", "completed", "failed", "cancelled"},
    "waiting_tool": {"running", "paused", "cancel_requested", "completed", "failed", "cancelled"},
    "paused": {"running", "cancel_requested", "cancelled", "failed"},
    "cancel_requested": {"cancelled"},  # cancellation wins late provider races,
    "completed": {"running"},
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


def cancel_registered_run_task(run_id: str) -> bool:
    """Cancel an in-process durable worker when one is registered.

    Database cancellation remains the cross-process source of truth. This
    eager cancellation only shortens the local path when the API and worker
    share an event loop; the worker still finalizes state in its own handler.
    """
    task = _ACTIVE_RUN_TASKS.get(str(run_id))
    if task is None or task.done():
        _ACTIVE_RUN_TASKS.pop(str(run_id), None)
        return False
    try:
        worker_loop = task.get_loop()
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is worker_loop:
            task.cancel()
        else:
            worker_loop.call_soon_threadsafe(task.cancel)
        return True
    except (RuntimeError, AttributeError):
        return False


def touch_agent_run(
    db: Session,
    run_id: str,
    *,
    step: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> AgentRun | None:
    """Refresh a run heartbeat and merge small runtime metadata."""
    run = db.query(AgentRun).filter(AgentRun.id == str(run_id)).one_or_none()
    if run is None:
        return None
    now = _now()
    run.heartbeat_at = now
    run.updated_at = now
    if step is not None:
        run.current_step = max(int(run.current_step or 0), int(step))
    if metadata:
        current = dict(run.run_metadata or {})
        current.update(_safe_payload(metadata))
        run.run_metadata = current
    return run


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
    # Persist any event appended earlier in this transaction before refreshing
    # the run. Otherwise refresh can restore a stale event_sequence and the
    # pending event insert will collide with the next sequence number.
    db.flush()
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
    if target_status == "running":
        if run.started_at is None:
            run.started_at = now
        run.finished_at = None
    if target_status in TERMINAL_RUN_STATUSES:
        run.finished_at = now
    return append_run_event(
        db,
        run,
        event_type or f"run_{target_status}",
        payload,
    )



def compatibility_job_status(run_status: str | None) -> str:
    """Translate the authoritative AgentRun state to the legacy Job state."""
    return RUN_TO_JOB_STATUS.get(str(run_status or ""), "failed")


def sync_compatibility_jobs(
    db: Session,
    run: AgentRun,
    *,
    error: str | None = None,
    progress: list[dict[str, Any]] | None = None,
) -> list[Any]:
    """Project one AgentRun into its compatibility Job row(s).

    ``AgentRun`` owns lifecycle state. Jobs remain available to older API
    clients, but they cannot independently move a linked run back to an
    active state. This helper intentionally does not commit; callers can
    include the projection in the same transaction as the run transition.
    """
    from app.models.project import Job

    run_id = str(run.id)
    jobs = db.query(Job).filter(Job.agent_run_id == run_id).all()
    metadata = run.run_metadata if isinstance(run.run_metadata, dict) else {}
    metadata_job_id = str(metadata.get("job_id") or "").strip()
    if not jobs and metadata_job_id:
        job = db.query(Job).filter(Job.id == metadata_job_id).one_or_none()
        if job is not None:
            jobs = [job]
    if not jobs:
        return []

    status = compatibility_job_status(run.status)
    result = run.result_payload if isinstance(run.result_payload, dict) else {}
    result_error = str(result.get("error") or "").strip() or None
    now = _now()
    for job in jobs:
        job.agent_run_id = run_id
        job.status = status
        if progress is not None:
            job.progress = progress
        if error is not None:
            job.error = str(error)[:4000]
        elif status == "failed":
            job.error = result_error or job.error or "Agent run failed"
        elif status == "cancelled":
            job.error = None
        elif status == "paused":
            job.error = result_error or job.error or "Run paused; it can be resumed."
        elif status in {"pending", "running", "cancel_requested"}:
            job.error = None

        if run.started_at is not None and job.started_at is None:
            job.started_at = run.started_at
        if status in {"pending", "running", "cancel_requested"}:
            job.finished_at = None
        elif status in {"completed", "failed", "cancelled", "paused"}:
            job.finished_at = run.finished_at or job.finished_at or now
        job.heartbeat_at = run.heartbeat_at or now
        job.updated_at = now
    return jobs


def sync_project_from_agent_run(
    db: Session,
    run: AgentRun,
    project: Any | None = None,
    *,
    summary: str | None = None,
    details: dict[str, Any] | None = None,
    force: bool = False,
    commit: bool = True,
    publish: bool = True,
) -> Any | None:
    """Project a workspace run lifecycle onto the legacy Project fields.

    ``AgentRun`` is authoritative. ``Project.status``, ``agent_state`` and
    compact runtime memory are read-model fields for older routes and the UI.
    Pipeline runs carry their phase in ``metadata.job_type``. Non-pipeline
    workspace turns leave project status unchanged unless ``force`` is used
    for explicit terminal cancellation cleanup.
    """
    if str(getattr(run, "surface", "") or "") != "workspace":
        return project
    project_id = str(getattr(run, "project_id", "") or "").strip()
    if not project_id:
        return project
    if project is None:
        from app.models.project import Project

        project = db.query(Project).filter(Project.id == project_id).one_or_none()
    if project is None:
        return None

    metadata = run.run_metadata if isinstance(run.run_metadata, dict) else {}
    job_type = str(metadata.get("job_type") or "").strip().lower()
    if not job_type and str(run.kind or "").startswith("pipeline_"):
        job_type = str(run.kind).split("_", 1)[1].strip().lower()
    is_pipeline = job_type in {"generate", "render", "edit"} or str(run.kind or "").startswith("pipeline_")
    if not is_pipeline and not force:
        return project

    run_status = str(run.status or "queued").strip().lower()
    phase_by_job = {
        "generate": ("generating", "generating"),
        "render": ("rendering", "rendering"),
        # Editing changes source and then renders it, so the project remains
        # in the rendering bucket while the agent state identifies the edit.
        "edit": ("rendering", "editing"),
    }
    target_status = str(project.status or "created")
    target_state = str(project.agent_state or "idle")
    target_summary = summary
    if run_status in {"queued", "running", "waiting_tool", "cancel_requested"}:
        phase = phase_by_job.get(job_type)
        if phase is None:
            return project
        target_status, target_state = phase
    elif run_status == "paused":
        pending = bool((project.agent_memory or {}).get("pending_clarifications"))
        if pending:
            target_status, target_state = "needs_clarification", "needs_clarification"
            target_summary = target_summary or "Waiting for your decision"
        else:
            # A stale/interrupted pipeline is no longer running. Do not leave
            # the project in a busy status that makes the UI spin forever.
            if target_status in {"generating", "rendering", "editing"}:
                target_status = "created"
            target_state = "idle"
            target_summary = target_summary or "Run paused; retry to resume"
    elif run_status == "failed":
        target_status, target_state = "failed", "failed"
        result = run.result_payload if isinstance(run.result_payload, dict) else {}
        target_summary = target_summary or str(result.get("error") or "OpenCode failed")[:200]
    elif run_status == "completed":
        target_status, target_state = "completed", "idle"
        target_summary = target_summary or "Coding agent finished this workspace turn"
    elif run_status == "cancelled":
        from pathlib import Path
        from app.config import settings

        project_dir = Path(project.project_dir or (Path(settings.projects_dir) / project_id))
        has_workspace = (project_dir / "code").is_dir() or (project_dir / "output").is_dir()
        target_status, target_state = ("completed" if has_workspace else "created"), "idle"
        target_summary = target_summary or "Run cancelled"
    else:
        return project

    memory = dict(project.agent_memory or {})
    memory["state"] = target_state
    memory["updated_at"] = _now().isoformat()
    if target_state in {"generating", "rendering", "editing"}:
        memory.pop("summary", None)
    elif target_summary:
        memory["summary"] = str(target_summary)[:1000]
    if details:
        memory.setdefault("details", {}).update(details)
    project.status = target_status
    project.agent_state = target_state
    project.agent_memory = memory
    if commit:
        db.commit()
    if publish:
        from app.services.job_events import publish_project_event

        publish_project_event(
            project_id,
            {
                "agent_state": target_state,
                "project_status": target_status,
                "summary": target_summary,
                "run_id": str(run.id),
                "run_status": run_status,
            },
        )
    return project


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
    if event_type == "tool_started":
        run.resumable = False
    if event_type == "note_cell" and event.get("role") != "user":
        run.resumable = False
    # token_chunk rows must keep their text so the durable-replay transport
    # can stream them to clients; other events never carry raw tokens.
    if event_type == "token_chunk":
        payload: dict[str, Any] = dict(event)
    else:
        payload = {key: value for key, value in event.items() if key != "token"}
    return append_run_event(
        db,
        run,
        event_type,
        payload,
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


def finalize_cancelled_run(
    db: Session,
    run: AgentRun,
    *,
    reason: str = "user requested cancellation",
) -> AgentRun:
    """Finalize cancellation immediately after the provider abort is sent.

    The worker may be in another process (or may have died), so waiting for it
    to write the terminal state leaves the UI stuck on ``Stopping``.  This
    helper is idempotent and safe for a live worker: its polling predicate also
    sees ``cancel_requested`` and the worker's eventual finalizer becomes a
    no-op against the already-terminal run.
    """
    run = lock_agent_run(db, run)
    # A terminal completion or failure that won the race is immutable.
    # Never rewrite it as a cancellation after the fact.
    if run.status in {"completed", "failed"}:
        return run
    if run.status != "cancelled":
        run.cancel_requested = True
        transition_agent_run(
            db,
            run,
            "cancelled",
            event_type="run_cancelled",
            payload={"reason": str(reason)[:1000]},
        )
    metadata = dict(run.run_metadata or {})
    metadata["cancelled_by"] = "user"
    metadata["cancellation_reason"] = str(reason)[:1000]
    run.run_metadata = metadata
    run.result_payload = {"message": "Run cancelled.", "status": "cancelled"}
    return run



def cancel_agent_run(
    db: Session,
    run: AgentRun,
    *,
    reason: str = "user requested cancellation",
) -> AgentRun:
    """Cancel one run and project the terminal state to compatibility jobs.

    The operation is idempotent and keeps the durable run as the only
    lifecycle authority. Provider aborts and in-process task cancellation are
    best-effort side effects performed by the API after this state is stored.
    """
    run = request_run_cancel(db, run)
    if run.status not in {"completed", "failed"}:
        run = finalize_cancelled_run(db, run, reason=reason)
    sync_compatibility_jobs(db, run)
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


def recover_streamed_workspace_message(db: Session, run: AgentRun) -> bool:
    """Restore assistant text recorded in run events after an interrupted turn."""
    if str(getattr(run, "surface", "") or "") != "workspace" or not run.project_id:
        return False
    from app.models.project import ProjectMessage

    existing = (
        db.query(ProjectMessage.id)
        .filter(
            ProjectMessage.project_id == str(run.project_id),
            ProjectMessage.execution_id == str(run.id),
            ProjectMessage.role == "assistant",
        )
        .first()
    )
    if existing is not None:
        return False
    events = (
        db.query(RunEvent)
        .filter(
            RunEvent.run_id == str(run.id),
            RunEvent.event_type == "token_chunk",
        )
        .order_by(RunEvent.sequence.asc())
        .all()
    )
    content = "".join(
        str((event.event_payload or {}).get("token") or "")
        for event in events
        if isinstance(event.event_payload, dict)
    ).strip()
    if not content:
        return False
    db.add(
        ProjectMessage(
            id=str(uuid.uuid4()),
            project_id=str(run.project_id),
            role="assistant",
            kind="message",
            content=content,
            message_metadata={"streamed": True, "partial": True, "recovered": True},
            cell_id=str(uuid.uuid4()),
            cell_type="markdown",
            cell_revision=1,
            execution_id=str(run.id),
        )
    )
    return True


def purge_expired_agent_runs(
    db: Session,
    *,
    retention_days: float = 90,
    batch_size: int = 500,
    now: datetime | None = None,
) -> dict[str, int]:
    """Delete bounded terminal-run history while preserving live work.

    Project files, messages, and reports are not touched. Only terminal
    ``AgentRun`` rows older than the retention window are removed, together
    with their ordered events and telemetry samples. Paused and non-terminal
    runs are always retained so they remain resumable or recoverable.
    Compatibility ``Job`` rows stay visible as history but are detached from
    deleted runs to avoid dangling references.
    """
    try:
        days = float(retention_days)
    except (TypeError, ValueError):
        days = 0.0
    if days <= 0:
        return {
            "runs_deleted": 0,
            "events_deleted": 0,
            "telemetry_deleted": 0,
            "jobs_detached": 0,
        }
    try:
        limit = max(1, min(int(batch_size), 5_000))
    except (TypeError, ValueError):
        limit = 500

    reference = now or _now()
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    cutoff = reference - timedelta(days=days)
    active_continuations = ("waiting", "ready", "running", "failed")
    candidates = (
        db.query(AgentRun)
        .filter(
            AgentRun.status.in_(TERMINAL_RUN_STATUSES),
            func.coalesce(
                AgentRun.finished_at,
                AgentRun.updated_at,
                AgentRun.created_at,
            ) < cutoff,
            or_(
                AgentRun.continuation_status.is_(None),
                ~AgentRun.continuation_status.in_(active_continuations),
            ),
        )
        .order_by(
            func.coalesce(
                AgentRun.finished_at,
                AgentRun.updated_at,
                AgentRun.created_at,
            ).asc(),
            AgentRun.id.asc(),
        )
        .with_for_update()
        .limit(limit)
        .all()
    )
    counts = {
        "runs_deleted": 0,
        "events_deleted": 0,
        "telemetry_deleted": 0,
        "jobs_detached": 0,
    }
    from app.models.project import Job

    for run in candidates:
        # Older runs may carry a continuation plan in JSON before the indexed
        # continuation_status column was populated. Preserve active plans so
        # they can be recovered before their retention window is reconsidered.
        metadata = run.run_metadata if isinstance(run.run_metadata, dict) else {}
        plan = metadata.get("continuation_plan")
        if isinstance(plan, dict) and str(plan.get("status") or "").lower() in active_continuations:
            continue
        run_id = str(run.id)
        # Keep compatibility jobs as bounded project history, but make it
        # explicit that their durable run no longer exists.
        sync_compatibility_jobs(db, run)
        detached = (
            db.query(Job)
            .filter(Job.agent_run_id == run_id)
            .update({"agent_run_id": None}, synchronize_session=False)
        )
        counts["jobs_detached"] += int(detached or 0)
        counts["events_deleted"] += int(
            db.query(RunEvent)
            .filter(RunEvent.run_id == run_id)
            .delete(synchronize_session=False)
            or 0
        )
        counts["telemetry_deleted"] += int(
            db.query(RunTelemetry)
            .filter(RunTelemetry.run_id == run_id)
            .delete(synchronize_session=False)
            or 0
        )
        db.delete(run)
        counts["runs_deleted"] += 1
    if counts["runs_deleted"]:
        db.commit()
    return counts


def pause_stale_agent_runs(db: Session, *, stale_after_seconds: float = 300) -> int:
    """Mark orphaned non-terminal workers paused during process startup."""
    cutoff = _now() - timedelta(seconds=max(1, float(stale_after_seconds)))
    runs = (
        db.query(AgentRun)
        .filter(
            AgentRun.status.in_({"queued", "running", "waiting_tool", "cancel_requested", "paused"}),
            or_(AgentRun.heartbeat_at.is_(None), AgentRun.heartbeat_at < cutoff),
        )
        .with_for_update()
        .all()
    )
    changed = 0
    for run in runs:
        target = "cancelled" if run.cancel_requested else "paused"
        transitioned = run.status != target
        if transitioned:
            transition_agent_run(
                db,
                run,
                target,
                event_type="run_cancelled" if target == "cancelled" else "run_paused",
                payload={"reason": "stale worker detected"},
            )
        sync_compatibility_jobs(db, run)
        if recover_streamed_workspace_message(db, run):
            changed += 1
        if run.project_id:
            from app.models.project import Project

            stale_project = db.query(Project).filter(Project.id == str(run.project_id)).one_or_none()
            if stale_project is not None:
                before_project = (stale_project.status, stale_project.agent_state, stale_project.agent_memory)
                sync_project_from_agent_run(
                    db,
                    run,
                    stale_project,
                    summary="Run cancelled" if target == "cancelled" else None,
                    commit=False,
                    publish=False,
                )
                after_project = (stale_project.status, stale_project.agent_state, stale_project.agent_memory)
                if before_project != after_project:
                    changed += 1
        if transitioned:
            changed += 1
    if changed:
        db.commit()
    return changed



def reconcile_stale_jobs(db: Session, *, stale_after_seconds: float = 300) -> int:
    """Reconcile orphaned compatibility jobs from the authoritative run.

    A linked Job is never independently paused or completed. If its run is
    stale, the run is transitioned first and the Job is then projected from
    that state. Unlinked legacy jobs retain the old pause behavior.
    """
    from app.models.project import Job

    cutoff = _now() - timedelta(seconds=max(1, float(stale_after_seconds)))
    jobs = (
        db.query(Job)
        .filter(
            Job.status.in_({"pending", "running", "cancel_requested"}),
            or_(Job.heartbeat_at < cutoff, Job.updated_at < cutoff),
        )
        .with_for_update()
        .all()
    )
    changed = 0
    now = _now()
    for job in jobs:
        run = None
        if job.agent_run_id:
            run = (
                db.query(AgentRun)
                .filter(AgentRun.id == str(job.agent_run_id))
                .with_for_update()
                .one_or_none()
            )
        if run is not None:
            heartbeat = run.heartbeat_at
            if heartbeat is not None and heartbeat.tzinfo is None:
                heartbeat = heartbeat.replace(tzinfo=timezone.utc)
            run_stale = heartbeat is None or heartbeat < cutoff
            if run.status not in TERMINAL_RUN_STATUSES and run.status != "paused" and run_stale:
                target = "cancelled" if run.cancel_requested else "paused"
                transition_agent_run(
                    db,
                    run,
                    target,
                    event_type="run_cancelled" if target == "cancelled" else "run_paused",
                    payload={"reason": "stale worker detected"},
                )
            before = (job.status, job.error, job.finished_at)
            sync_compatibility_jobs(db, run)
            if run.project_id:
                from app.models.project import Project

                stale_project = db.query(Project).filter(Project.id == str(run.project_id)).one_or_none()
                if stale_project is not None:
                    sync_project_from_agent_run(
                        db,
                        run,
                        stale_project,
                        summary="Run cancelled" if str(run.status) == "cancelled" else None,
                        commit=False,
                        publish=False,
                    )
            after = (job.status, job.error, job.finished_at)
            if before != after or run_stale:
                changed += 1
            continue

        # Jobs created before AgentRun linking still need a safe terminal
        # projection so the old endpoint does not remain stuck forever.
        job.status = "cancelled" if str(job.status) == "cancel_requested" else "paused"
        job.error = None if job.status == "cancelled" else "Worker heartbeat expired; run can be resumed."
        job.heartbeat_at = now
        job.finished_at = now
        job.updated_at = now
        changed += 1
    if changed:
        db.commit()
    return changed


def serialize_agent_run(run: AgentRun) -> dict[str, Any]:
    metadata = run.run_metadata if isinstance(run.run_metadata, dict) else {}
    usage = metadata.get("usage_totals") if isinstance(metadata.get("usage_totals"), dict) else {}
    measurements = metadata.get("measurements") if isinstance(metadata.get("measurements"), dict) else {}
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
        "usage": {
            key: value
            for key, value in usage.items()
            if key in {"input_tokens", "output_tokens", "total_tokens", "cost"}
        },
        "measurements": measurements,
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
            # Capture the sequence in the same snapshot as the terminal status.
            # Replay must drain events appended just before that status became
            # visible; this also makes the transport safe on shared SQLite
            # connections where another session can observe the status first.
            event_sequence = int(run.event_sequence or 0)
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
        from app.services.agent_plans import get_continuation_plan

        continuation = get_continuation_plan(run)
        if status in TERMINAL_RUN_STATUSES:
            # A worker publishes its public completion event and lifecycle
            # event in order. Do not close the stream until every event that
            # was already counted by the terminal run has been replayed.
            if (continuation and continuation.get("status") == "running") or cursor < event_sequence:
                await asyncio.sleep(0.01)
                continue
            return
        if status == "paused":
            yield {
                "type": "paused",
                "status": "paused",
                "continuation_status": continuation.get("status") if continuation else None,
                "message": "This run was paused after the server lost its worker. Retry the same request to resume when it is safe, or continue with a new turn.",
                "run_id": run_id,
                "sequence": cursor,
            }
            return
        await asyncio.sleep(0.25)


__all__ = [
    "TERMINAL_RUN_STATUSES",
    "RUN_TO_JOB_STATUS",
    "RUN_TRANSITIONS",
    "IdempotencyConflict",
    "RunTransitionError",
    "create_or_get_agent_run",
    "append_run_event",
    "transition_agent_run",
    "compatibility_job_status",
    "sync_compatibility_jobs",
    "sync_project_from_agent_run",
    "record_stream_event",
    "record_run_telemetry",
    "telemetry_from_usage",
    "approximate_tokens",
    "request_run_cancel",
    "finalize_cancelled_run",
    "cancel_agent_run",
    "run_cancel_requested",
    "get_agent_run",
    "serialize_agent_run",
    "purge_expired_agent_runs",
    "recover_streamed_workspace_message",
    "pause_stale_agent_runs",
    "reconcile_stale_jobs",
    "list_run_events",
    "serialize_run_telemetry",
    "list_run_telemetry",
    "replay_agent_run_stream",
    "session_factory_for",
    "register_run_task",
    "unregister_run_task",
    "is_run_task_active",
    "cancel_registered_run_task",
    "touch_agent_run",
    "normalize_idempotency_key",
    "request_fingerprint",
]

