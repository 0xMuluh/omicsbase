"""Durable provenance records for ReportPack and Quarto executions.

The runner executes untrusted project code in its existing sandbox.  This
module only records evidence around that execution: immutable input hashes,
step/validator lifecycle, declared artifact hashes, and bounded log evidence.
Records are append-only per run and retained under the project workspace so a
review can answer what code produced a report without trusting mutable UI state.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app.services.execution_contract import ExecutionContractError, load_execution_contract

PROVENANCE_SCHEMA_VERSION = "1.0"
PROVENANCE_DIR = Path(".omicsbase") / "execution_runs"
MAX_EVIDENCE_CHARS = 2_000
MAX_EVENTS = 500
MAX_RUN_RECORDS = 50
_RUN_ID = re.compile(r"^[a-f0-9]{16,64}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str | None:
    try:
        if not path.is_file() or path.is_symlink():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _bounded(value: Any, limit: int = MAX_EVIDENCE_CHARS) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def snapshot_execution_inputs(project_dir: str | Path) -> dict[str, Any]:
    """Capture hashes before execution starts; never include source contents."""
    root = Path(project_dir).resolve()
    result: dict[str, Any] = {
        "contract_sha256": _sha256(root / "execution_contract.json"),
        "capability_contract_sha256": _sha256(root / ".omicsbase" / "capabilities.json"),
        "steps": {},
    }
    try:
        contract = load_execution_contract(root)
    except (ExecutionContractError, OSError):
        return result
    if contract is None:
        return result
    for step in contract.steps:
        result["steps"][step.step_id] = {
            "path": step.path,
            "role": step.role,
            "sha256": _sha256(root / step.path),
        }
    if contract.entrypoint:
        result["entrypoint"] = {
            "path": contract.entrypoint,
            "sha256": _sha256(root / contract.entrypoint),
        }
    return result


def _artifact_evidence(root: Path, paths: Iterable[str]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for relative in paths:
        path = root / str(relative)
        try:
            size = path.stat().st_size if path.is_file() else None
        except OSError:
            size = None
        evidence.append({
            "path": str(relative),
            "exists": bool(path.is_file() and not path.is_symlink()),
            "sha256": _sha256(path),
            "size_bytes": size,
        })
    return evidence


def _prune_records(directory: Path, *, keep: int = MAX_RUN_RECORDS) -> None:
    records = sorted(
        (path for path in directory.glob("*.json") if path.name != "latest.json"),
        key=lambda path: path.stat().st_mtime_ns if path.exists() else 0,
        reverse=True,
    )
    for path in records[keep:]:
        try:
            path.unlink()
        except OSError:
            continue


def write_execution_provenance(
    project_dir: str | Path,
    *,
    run_id: str,
    started_at: str,
    result: dict[str, Any] | None,
    events: Iterable[dict[str, Any]],
    input_snapshot: dict[str, Any] | None = None,
    resume_from_step: str | None = None,
    target_pages: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Write one bounded, self-contained execution evidence record."""
    if not _RUN_ID.fullmatch(str(run_id)):
        raise ValueError("Invalid execution provenance run id")
    root = Path(project_dir).resolve()
    run_result = result if isinstance(result, dict) else {"status": "failed"}
    event_list = []
    for event in list(events)[-MAX_EVENTS:]:
        if not isinstance(event, dict):
            continue
        event_list.append({
            "step": _bounded(event.get("step"), 200),
            "status": _bounded(event.get("status"), 40),
            "time": _bounded(event.get("time"), 80),
            "detail": _bounded(event.get("detail"), MAX_EVIDENCE_CHARS),
        })

    try:
        contract = load_execution_contract(root)
    except (ExecutionContractError, OSError):
        contract = None
    step_snapshot = (input_snapshot or {}).get("steps") or {}
    event_by_step: dict[str, list[dict[str, Any]]] = {}
    for event in event_list:
        event_by_step.setdefault(str(event.get("step") or ""), []).append(event)

    steps: list[dict[str, Any]] = []
    validators: list[dict[str, Any]] = []
    if contract is not None:
        for step in contract.steps:
            key = f"pack_{step.step_id}"
            step_events = event_by_step.get(key, [])
            final = step_events[-1] if step_events else None
            status = str(final.get("status") or "not_run") if final else "not_run"
            snapshot = step_snapshot.get(step.step_id) or {}
            evidence = {
                "step_id": step.step_id,
                "path": step.path,
                "role": step.role,
                "status": status,
                "input_sha256": snapshot.get("sha256"),
                "events": step_events[-8:],
            }
            steps.append(evidence)
            if step.role == "validator":
                validators.append({
                    "step_id": step.step_id,
                    "path": step.path,
                    "status": status,
                    "input_sha256": snapshot.get("sha256"),
                    "evidence": step_events[-8:],
                })

    artifact_paths = list(contract.artifacts) if contract is not None else ["output/index.html"]
    finished_at = _now()
    record: dict[str, Any] = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "run_id": str(run_id),
        "started_at": started_at,
        "finished_at": finished_at,
        "status": str(run_result.get("status") or "failed"),
        "resume_from_step": resume_from_step,
        "target_pages": [str(value) for value in (target_pages or ()) if str(value).strip()],
        "input_snapshot": input_snapshot or {},
        "steps": steps,
        "validators": validators,
        "artifacts": _artifact_evidence(root, artifact_paths),
        "errors": [item for item in (run_result.get("errors") or []) if isinstance(item, dict)][-20:],
        "events": event_list,
    }
    directory = root / PROVENANCE_DIR
    _atomic_json(directory / f"{run_id}.json", record)
    _atomic_json(directory / "latest.json", {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "run_id": str(run_id),
        "finished_at": finished_at,
        "status": record["status"],
    })
    _prune_records(directory)
    return record


def _safe_run_path(project_dir: str | Path, run_id: str) -> Path:
    if not _RUN_ID.fullmatch(str(run_id)):
        raise ValueError("Invalid execution provenance run id")
    return Path(project_dir).resolve() / PROVENANCE_DIR / f"{run_id}.json"


def read_execution_provenance(project_dir: str | Path, run_id: str) -> dict[str, Any]:
    path = _safe_run_path(project_dir, run_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FileNotFoundError(run_id) from exc
    if not isinstance(payload, dict) or payload.get("run_id") != run_id:
        raise FileNotFoundError(run_id)
    return payload


def list_execution_provenance(project_dir: str | Path, *, limit: int = 50) -> list[dict[str, Any]]:
    directory = Path(project_dir).resolve() / PROVENANCE_DIR
    if not directory.is_dir():
        return []
    result: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json"), key=lambda item: item.stat().st_mtime_ns, reverse=True):
        if path.name == "latest.json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("run_id"), str):
            result.append({
                "run_id": payload["run_id"],
                "started_at": payload.get("started_at"),
                "finished_at": payload.get("finished_at"),
                "status": payload.get("status"),
                "resume_from_step": payload.get("resume_from_step"),
                "target_pages": payload.get("target_pages") or [],
                "validators": payload.get("validators") or [],
                "artifacts": payload.get("artifacts") or [],
            })
        if len(result) >= max(1, min(int(limit), MAX_RUN_RECORDS)):
            break
    return result


__all__ = [
    "MAX_RUN_RECORDS",
    "PROVENANCE_DIR",
    "PROVENANCE_SCHEMA_VERSION",
    "list_execution_provenance",
    "read_execution_provenance",
    "snapshot_execution_inputs",
    "write_execution_provenance",
]
