"""Synchronize the filesystem edit journal into the ProjectEdit DB index."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models.project import ProjectEdit


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if text:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _manifest_payload(project, manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_id": project.id,
        "transaction_id": str(manifest.get("transaction_id") or ""),
        "origin": str(manifest.get("origin") or "agent")[:50],
        "summary": str(manifest.get("summary") or "")[:4000],
        "status": str(manifest.get("status") or "committed")[:32],
        "files": list(manifest.get("files") or []),
        "diagnostics": list(manifest.get("diagnostics") or []),
        "created_at": _timestamp(manifest.get("created_at")),
        "committed_at": _timestamp(manifest.get("committed_at") or manifest.get("created_at")),
        "reverted_by": manifest.get("reverted_by"),
    }


def record_project_edit(db, project, result: Any) -> ProjectEdit | None:
    """Upsert one committed transaction without duplicating retries."""
    transaction_id = str(getattr(result, "transaction_id", "") or "")
    if not transaction_id:
        return None
    payload = {
        "transaction_id": transaction_id,
        "origin": str(getattr(result, "origin", None) or "agent"),
        "summary": str(getattr(result, "summary", None) or ""),
        "status": str(getattr(result, "status", None) or "committed"),
        "files": list(getattr(result, "to_dict", lambda: {})().get("files") or []),
        "diagnostics": list(getattr(result, "diagnostics", None) or []),
        "created_at": datetime.now(timezone.utc),
        "committed_at": datetime.now(timezone.utc),
    }
    row = db.query(ProjectEdit).filter(ProjectEdit.transaction_id == transaction_id).one_or_none()
    if row is None:
        row = ProjectEdit(project_id=project.id, **payload)
        db.add(row)
    else:
        # A transaction id is globally unique; never let a caller rebind it to
        # another tenant/project. Refresh mutable status only for its owner.
        if str(row.project_id) != str(project.id):
            raise ValueError("Edit transaction belongs to another project")
        for key, value in payload.items():
            if key != "transaction_id":
                setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


def sync_project_edits(db, project) -> int:
    """Index every valid journal manifest for a project; return rows touched."""
    root = Path(project.project_dir).resolve() / ".omicsbase" / "edits" if project.project_dir else None
    if root is None or not root.is_dir():
        return 0
    touched = 0
    for directory in sorted(root.iterdir()):
        manifest_path = directory / "manifest.json"
        if not directory.is_dir() or not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        transaction_id = str(manifest.get("transaction_id") or directory.name)
        if transaction_id != directory.name:
            continue
        manifest["transaction_id"] = transaction_id
        payload = _manifest_payload(project, manifest)
        row = db.query(ProjectEdit).filter(ProjectEdit.transaction_id == transaction_id).one_or_none()
        if row is None:
            db.add(ProjectEdit(**payload))
        elif str(row.project_id) == str(project.id):
            for key, value in payload.items():
                setattr(row, key, value)
        else:
            continue
        touched += 1
    if touched:
        db.commit()
    return touched


def project_edit_dict(row: ProjectEdit) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "project_id": str(row.project_id),
        "transaction_id": row.transaction_id,
        "origin": row.origin,
        "summary": row.summary,
        "status": row.status,
        "files": row.files or [],
        "diagnostics": row.diagnostics or [],
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "committed_at": row.committed_at.isoformat() if row.committed_at else None,
        "reverted_by": row.reverted_by,
    }


__all__ = ["record_project_edit", "sync_project_edits", "project_edit_dict"]
