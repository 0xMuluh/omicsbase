"""Project edit journal and safe undo endpoints."""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_tenant, get_project_for_tenant
from app.database import get_db
from app.services.edit_engine import EditBusy, EditEngineError, revert_transaction

router = APIRouter(prefix="/api/projects/{project_id}/edits", tags=["edits"])
_TRANSACTION_ID = re.compile(r"^[a-f0-9]{16,64}$")


def _project_edits_dir(project) -> Path:
    if not project.project_dir:
        raise HTTPException(status_code=404, detail="Project workspace is not generated")
    return Path(project.project_dir).resolve() / ".omicsbase" / "edits"


def _safe_transaction_dir(project, transaction_id: str) -> Path:
    if not _TRANSACTION_ID.fullmatch(transaction_id):
        raise HTTPException(status_code=400, detail="Invalid edit transaction id")
    root = _project_edits_dir(project)
    candidate = (root / transaction_id).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Invalid edit transaction path") from exc
    return candidate


def _read_manifest(path: Path) -> dict[str, Any]:
    manifest_path = path / "manifest.json"
    if not manifest_path.is_file():
        raise HTTPException(status_code=404, detail="Edit transaction was not found")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="Edit transaction journal is unreadable") from exc
    manifest["transaction_id"] = path.name
    before_root = path / "before"
    after_root = path / "after"
    for item in manifest.get("files") or []:
        relative = str(item.get("path") or "")
        before_path = before_root / relative
        after_path = after_root / relative
        try:
            before = before_path.read_text(encoding="utf-8").splitlines(keepends=True) if before_path.is_file() else []
            after = after_path.read_text(encoding="utf-8").splitlines(keepends=True) if after_path.is_file() else []
            diff = "".join(difflib.unified_diff(before, after, fromfile=f"a/{relative}", tofile=f"b/{relative}", n=3))
            item["diff"] = diff[:100_000]
            if len(diff) > 100_000:
                item["diff_truncated"] = True
        except (OSError, UnicodeDecodeError):
            item["diff"] = None
    return manifest


@router.get("")
def list_edit_transactions(
    project_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    project = get_project_for_tenant(db, project_id, tenant_id)
    from app.services.project_edit_index import sync_project_edits
    sync_project_edits(db, project)
    root = _project_edits_dir(project)
    if not root.is_dir():
        return {"transactions": []}
    transactions = []
    for directory in root.iterdir():
        if directory.is_dir() and _TRANSACTION_ID.fullmatch(directory.name) and (directory / "manifest.json").is_file():
            transactions.append(_read_manifest(directory))
    transactions.sort(
        key=lambda item: str(item.get("created_at") or item.get("transaction_id") or ""),
        reverse=True,
    )
    return {"transactions": transactions[:200]}


@router.get("/{transaction_id}")
def get_edit_transaction(
    project_id: str,
    transaction_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    project = get_project_for_tenant(db, project_id, tenant_id)
    from app.services.project_edit_index import sync_project_edits
    sync_project_edits(db, project)
    return _read_manifest(_safe_transaction_dir(project, transaction_id))


@router.post("/{transaction_id}/revert")
def revert_edit_transaction(
    project_id: str,
    transaction_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    project = get_project_for_tenant(db, project_id, tenant_id)
    _safe_transaction_dir(project, transaction_id)
    try:
        result = revert_transaction(project.project_dir, transaction_id, lock_timeout=0)
    except EditBusy as exc:
        raise HTTPException(status_code=423, detail=exc.to_dict()) from exc
    except EditEngineError as exc:
        raise HTTPException(status_code=409 if exc.code == "edit_conflict" else 400, detail=exc.to_dict()) from exc

    from app.services.agent_runtime import record_agent_action, refresh_project_memory

    refresh_project_memory(db, project)
    from app.services.project_edit_index import sync_project_edits
    sync_project_edits(db, project)
    record_agent_action(
        db,
        project,
        "file_edit",
        "completed",
        f"Reverted edit transaction {transaction_id}",
        {"transaction_id": transaction_id, "revert_transaction_id": result.transaction_id},
        files=[item.path for item in result.files],
    )
    return result.to_dict()
