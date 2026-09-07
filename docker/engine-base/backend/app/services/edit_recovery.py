"""Crash recovery for interrupted transactional edit journals."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.edit_engine import (
    EditEngineError,
    _project_lock,
    _read_journal_copy,
    _replace_bytes_atomic,
    _resolve_project_path,
    _write_json_atomic,
    sha256_bytes,
)

_RECOVERABLE = {"prepared", "committing"}


def _current_bytes(root: Path, relative: str) -> bytes | None:
    target = _resolve_project_path(root, relative)
    if target is None:
        raise EditEngineError("Edit journal path escaped the project root.", path=relative)
    return target.read_bytes() if target.is_file() else None


def _restore_before(root: Path, journal: Path, item: dict[str, Any]) -> None:
    relative = str(item.get("path") or "")
    target = _resolve_project_path(root, relative)
    if target is None:
        raise EditEngineError("Edit journal path escaped the project root.", path=relative)
    before = _read_journal_copy(journal / "before", relative)
    if before is None:
        target.unlink(missing_ok=True)
        return
    mode = target.stat().st_mode & 0o777 if target.exists() else 0o644
    _replace_bytes_atomic(target, before, mode=mode)


def _recover_one(root: Path, journal: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    items = [item for item in (manifest.get("files") or []) if isinstance(item, dict)]
    if not items:
        manifest["status"] = "abandoned"
        manifest["recovery"] = "No journal files were recorded."
        return manifest
    states: list[str] = []
    for item in items:
        relative = str(item.get("path") or "")
        current = _current_bytes(root, relative)
        actual = sha256_bytes(current)
        before_sha = item.get("before_sha256")
        after_sha = item.get("after_sha256")
        if actual == after_sha:
            states.append("after")
        elif actual == before_sha:
            states.append("before")
        else:
            states.append("conflict")
    if "conflict" in states:
        manifest["status"] = "recovery_conflict"
        manifest["recovery"] = "A file changed outside the journal; no recovery write was attempted."
        manifest["recovery_states"] = states
        return manifest

    if all(state == "after" for state in states):
        manifest["status"] = "committed"
        manifest["recovery"] = "The interrupted transaction was already fully applied."
        manifest["recovered_at"] = datetime.now(timezone.utc).isoformat()
        try:
            from app.services.edit_engine import _record_pending_invalidation
            manifest["invalidation"] = _record_pending_invalidation(
                root,
                [str(item.get("path") or "") for item in items],
            )
        except Exception as exc:  # pragma: no cover - metadata failure is non-fatal
            manifest["invalidation_error"] = str(exc)[:500]
        return manifest

    # Every remaining state is provably before/after. Restore the complete
    # transaction to before; this is the only safe outcome for a partial write.
    for item in items:
        _restore_before(root, journal, item)
    manifest["status"] = "abandoned" if manifest.get("status") == "prepared" else "rolled_back"
    manifest["recovery"] = "Recovered an interrupted transaction by restoring all before-images."
    manifest["recovery_states"] = states
    manifest["recovered_at"] = datetime.now(timezone.utc).isoformat()
    return manifest


def recover_edit_journals(
    project_dir: str | Path,
    *,
    transaction_id: str | None = None,
) -> list[dict[str, Any]]:
    """Recover interrupted journals without overwriting unknown file states."""
    root = Path(project_dir).resolve()
    directory = root / ".omicsbase" / "edits"
    if not directory.is_dir():
        return []
    candidates = [directory / transaction_id] if transaction_id else sorted(directory.iterdir())
    recovered: list[dict[str, Any]] = []
    with _project_lock(root):
        for journal in candidates:
            if not journal.is_dir() or not (journal / "manifest.json").is_file():
                continue
            try:
                manifest = json.loads((journal / "manifest.json").read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(manifest, dict) or manifest.get("status") not in _RECOVERABLE:
                continue
            manifest["transaction_id"] = str(manifest.get("transaction_id") or journal.name)
            updated = _recover_one(root, journal, manifest)
            _write_json_atomic(journal / "manifest.json", updated)
            recovered.append(updated)
    return recovered


__all__ = ["recover_edit_journals"]
