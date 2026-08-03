"""Persistent agent runtime state and audit history."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AGENT_STATES = {
    "idle",
    "planning",
    "needs_user",
    "generating",
    "rendering",
    "repairing",
    "editing",
    "reviewing",
    "completed",
    "failed",
}
MAX_ACTIONS = 200
MAX_FILES = 300
MAX_DURABLE_ITEMS = 40
DURABLE_CATEGORIES = {"preferences", "decisions", "constraints", "findings"}
NOTE_CELL_TYPES = frozenset({"markdown", "agent", "code", "output", "provenance"})
DEFAULT_MESSAGE_CELL_TYPES = {
    "user": "agent",
    "assistant": "markdown",
    "tool": "output",
}


def normalise_cell_type(cell_type: str | None, *, role: str | None = None) -> str | None:
    """Return a supported cell type without forcing legacy rows into cells."""
    value = str(cell_type or "").strip().lower()
    if not value:
        return DEFAULT_MESSAGE_CELL_TYPES.get(str(role or "").strip().lower())
    if value not in NOTE_CELL_TYPES:
        raise ValueError(f"Unsupported note cell type: {cell_type}")
    return value


def build_run_event_metadata(
    run_id: str,
    sequence: int,
    event_type: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Add a stable audit envelope to one persisted run event."""
    payload = dict(metadata or {})
    payload["run_id"] = run_id
    payload["sequence"] = sequence
    payload["event_type"] = event_type
    return payload


def record_project_message(
    db,
    project,
    role: str,
    content: str,
    *,
    kind: str = "message",
    metadata: dict[str, Any] | None = None,
    cell_id: str | None = None,
    cell_type: str | None = None,
    cell_revision: int | None = None,
    execution_id: str | None = None,
):
    """Persist one conversational message or tool event."""
    from app.models.project import ProjectMessage

    typed_cell_type = None
    if any((cell_id, cell_type, cell_revision, execution_id)):
        typed_cell_type = normalise_cell_type(cell_type, role=role)

    message = ProjectMessage(
        project_id=project.id,
        role=role,
        kind=kind,
        content=content,
        message_metadata=metadata,
        cell_id=cell_id,
        cell_type=typed_cell_type,
        cell_revision=cell_revision,
        execution_id=execution_id,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    from app.services.job_events import publish_project_event

    publish_project_event(
        str(project.id),
        {"latest_message_id": str(message.id), "role": role, "kind": kind},
    )
    return message


def update_durable_project_memory(
    db,
    project,
    updates: list[dict[str, Any]],
    *,
    source_message: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Merge explicit, typed memories without overwriting runtime state."""
    memory = _memory(project)
    durable = dict(memory.get("durable") or {})
    for category in DURABLE_CATEGORIES:
        durable[category] = list(durable.get(category) or [])

    changed = False
    for update in updates[:20]:
        if not isinstance(update, dict):
            continue
        category = _normalise_memory_category(str(update.get("category") or ""))
        content = " ".join(str(update.get("content") or "").split()).strip()
        if not category or len(content) < 4:
            continue
        source = " ".join(str(update.get("source") or "conversation").split())[:500]
        evidence = " ".join(str(update.get("evidence") or source_message or "").split())[:1000]
        key = content.casefold()
        items = durable[category]
        existing = next(
            (item for item in items if str(item.get("content") or "").casefold() == key),
            None,
        )
        if existing:
            existing.update({"source": source, "evidence": evidence, "updated_at": _now()})
        else:
            items.append(
                {
                    "content": content[:1000],
                    "source": source,
                    "evidence": evidence,
                    "created_at": _now(),
                    "updated_at": _now(),
                }
            )
        durable[category] = items[-MAX_DURABLE_ITEMS:]
        changed = True

    if changed:
        durable["updated_at"] = _now()
        memory["durable"] = durable
        memory["updated_at"] = _now()
        project.agent_memory = memory
        db.commit()
    return {
        category: durable.get(category, [])
        for category in sorted(DURABLE_CATEGORIES)
    }


def durable_project_memory(project) -> dict[str, list[dict[str, Any]]]:
    durable = (project.agent_memory or {}).get("durable") or {}
    return {
        category: list(durable.get(category) or [])
        for category in sorted(DURABLE_CATEGORIES)
    }


def set_agent_state(db, project, state: str, summary: str | None = None, details: dict[str, Any] | None = None) -> None:
    """Set current agent state and update compact memory."""
    if state not in AGENT_STATES:
        raise ValueError(f"Invalid agent state: {state}")

    memory = _memory(project)
    memory["state"] = state
    memory["updated_at"] = _now()
    if summary:
        memory["summary"] = summary
    if details:
        memory.setdefault("details", {}).update(details)

    project.agent_state = state
    project.agent_memory = memory
    db.commit()
    from app.services.job_events import publish_project_event

    publish_project_event(str(project.id), {"agent_state": state, "summary": summary})


def queue_pending_guidance(
    db,
    project,
    guidance: str,
    *,
    source: str = "user",
) -> dict[str, Any]:
    """Store mid-job steering to apply after the current run finishes."""
    content = " ".join(guidance.split()).strip()
    if len(content) < 3:
        raise ValueError("Guidance is empty.")

    memory = _memory(project)
    pending = list(memory.get("pending_guidance") or [])
    entry = {
        "content": content[:2000],
        "source": source,
        "created_at": _now(),
        "status": "queued",
    }
    pending.append(entry)
    memory["pending_guidance"] = pending[-20:]
    memory["updated_at"] = _now()
    project.agent_memory = memory
    db.commit()
    from app.services.job_events import publish_project_event

    publish_project_event(str(project.id), {"pending_guidance": True})
    return entry


def consume_pending_guidance(db, project) -> list[dict[str, Any]]:
    """Return and clear queued mid-job guidance."""
    memory = _memory(project)
    pending = list(memory.get("pending_guidance") or [])
    if not pending:
        return []
    memory["pending_guidance"] = []
    memory["updated_at"] = _now()
    project.agent_memory = memory
    db.commit()
    return pending


def record_agent_action(
    db,
    project,
    action_type: str,
    status: str,
    summary: str,
    details: dict[str, Any] | None = None,
    files: list[str] | None = None,
    job_id: str | None = None,
) -> None:
    """Append a structured, user-visible agent action."""
    action = {
        "time": _now(),
        "type": action_type,
        "status": status,
        "summary": summary,
    }
    if details:
        action["details"] = details
    if files:
        action["files"] = files
    if job_id:
        action["job_id"] = job_id

    actions = list(project.agent_actions or [])
    actions.append(action)
    project.agent_actions = actions[-MAX_ACTIONS:]
    db.commit()


def refresh_project_memory(db, project, files: list[Any] | None = None, jobs: list[Any] | None = None) -> None:
    """Refresh the compact project map used by agents and UI."""
    memory = _memory(project)
    memory["updated_at"] = _now()
    memory["project"] = {
        "id": str(project.id),
        "name": project.name,
        "status": project.status,
        "project_dir": project.project_dir,
    }
    if project.analysis_plan:
        memory["plan"] = _summarize_plan(project.analysis_plan)
    if files is not None:
        memory["uploaded_files"] = [_summarize_uploaded_file(file_record) for file_record in files]
    if project.project_dir:
        memory["generated_files"] = _summarize_project_files(Path(project.project_dir))
    if jobs is not None:
        memory["recent_jobs"] = [_summarize_job(job) for job in jobs[:10]]

    project.agent_memory = memory
    db.commit()


def _memory(project) -> dict[str, Any]:
    return dict(project.agent_memory or {})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalise_memory_category(category: str) -> str | None:
    normalized = category.strip().lower()
    aliases = {
        "preference": "preferences",
        "decision": "decisions",
        "constraint": "constraints",
        "finding": "findings",
        "fact": "findings",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in DURABLE_CATEGORIES else None


def _summarize_plan(plan: dict[str, Any]) -> dict[str, Any]:
    workflow = plan.get("workflow") or []
    return {
        "project_name": plan.get("project_name"),
        "study_type": plan.get("study_type"),
        "grouping_variable": plan.get("grouping_variable"),
        "group_levels": plan.get("group_levels") or [],
        "enabled_steps": [step.get("id") for step in workflow if step.get("enabled")],
        "contested_steps": [step.get("id") for step in workflow if step.get("classification") == "contested" and step.get("enabled")],
    }


def _summarize_uploaded_file(file_record) -> dict[str, Any]:
    summary = file_record.file_summary or {}
    return {
        "name": file_record.original_name,
        "role": file_record.file_role,
        "format": file_record.detected_format,
        "dimensions": summary.get("dimensions"),
        "columns": (summary.get("columns") or [])[:80],
    }


def _summarize_project_files(project_dir: Path) -> list[dict[str, Any]]:
    if not project_dir.exists():
        return []

    files = []
    for path in sorted(project_dir.rglob("*")):
        if len(files) >= MAX_FILES:
            break
        if not path.is_file():
            continue
        relative = path.relative_to(project_dir)
        if any(part.startswith(".") for part in relative.parts):
            continue
        files.append({
            "path": relative.as_posix(),
            "size": path.stat().st_size,
            "extension": path.suffix,
        })
    return files


def _summarize_job(job) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "type": job.job_type,
        "status": job.status,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "error": job.error,
    }
