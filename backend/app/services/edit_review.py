"""Prepare/approve edit proposals without bypassing the transactional kernel."""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app.services.edit_engine import (
    EditEngineError,
    EditOperation,
    EditPolicy,
    EditTransactionResult,
    PreparedTransaction,
    commit_transaction,
    prepare_transaction,
    sha256_file,
)

REVIEW_DIR = Path(".omicsbase") / "edit_reviews"
_REVIEW_ID = re.compile(r"^[a-f0-9]{16,64}$")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _review_path(project_dir: str | Path, review_id: str) -> Path:
    if not _REVIEW_ID.fullmatch(str(review_id)):
        raise ValueError("Invalid edit review id")
    root = Path(project_dir).resolve() / REVIEW_DIR
    candidate = (root / f"{review_id}.json").resolve()
    candidate.relative_to(root.resolve())
    return candidate


def _protected_paths(project_dir: Path) -> frozenset[str]:
    protected = {"execution_contract.json", "report_pack.yaml", ".omicsbase/capabilities.json"}
    try:
        from app.services.execution_contract import load_execution_contract
        contract = load_execution_contract(project_dir)
    except Exception:
        contract = None
    if contract is not None:
        protected.update(step.path for step in contract.steps if step.role == "validator")
    try:
        from app.services.capability_contract import load_capability_contract
        capabilities = load_capability_contract(project_dir)
    except Exception:
        capabilities = None
    if capabilities is not None:
        protected.update(
            path
            for item in capabilities.selected
            for path in item.capability.validators
        )
    return frozenset(protected)


def _bind_base_hashes(root: Path, operations: Iterable[EditOperation]) -> list[EditOperation]:
    bound: list[EditOperation] = []
    for operation in operations:
        if operation.path and operation.base_sha256 is None:
            target = (root / operation.path).resolve()
            target.relative_to(root)
            if target.is_file():
                operation = replace(operation, base_sha256=sha256_file(target))
        bound.append(operation)
    return bound


def _serialize_operation(operation: EditOperation) -> dict[str, Any]:
    return {
        "path": operation.path,
        "kind": operation.kind,
        "search": operation.search,
        "replace": operation.replace,
        "content": operation.content,
        "patch": operation.patch,
        "base_sha256": operation.base_sha256,
        "base_hashes": operation.base_hashes,
        "allow_multiple": operation.allow_multiple,
        "reason": operation.reason,
    }


def prepare_edit_review(
    project_dir: str | Path,
    operations: Iterable[EditOperation | dict[str, Any]],
    *,
    origin: str = "review",
    summary: str = "Edit proposal",
    policy: EditPolicy | None = None,
) -> dict[str, Any]:
    root = Path(project_dir).resolve()
    parsed = [item if isinstance(item, EditOperation) else EditOperation.from_payload(item) for item in operations]
    parsed = _bind_base_hashes(root, parsed)
    effective_policy = policy or EditPolicy(protected_paths=_protected_paths(root))
    prepared = prepare_transaction(
        root,
        parsed,
        origin=origin,
        summary=summary,
        policy=effective_policy,
        validate=True,
    )
    review_id = prepared.transaction_id
    payload = {
        "review_id": review_id,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "origin": origin,
        "summary": summary,
        "operations": [_serialize_operation(item) for item in parsed],
        "prepared": prepared.to_dict(include_diff=True),
    }
    _atomic_json(root / REVIEW_DIR / f"{review_id}.json", payload)
    return payload


def read_edit_review(project_dir: str | Path, review_id: str) -> dict[str, Any]:
    path = _review_path(project_dir, review_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FileNotFoundError(review_id) from exc
    if not isinstance(payload, dict) or payload.get("review_id") != review_id:
        raise FileNotFoundError(review_id)
    return payload


def list_edit_reviews(project_dir: str | Path, *, limit: int = 50) -> list[dict[str, Any]]:
    root = Path(project_dir).resolve() / REVIEW_DIR
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json"), key=lambda item: item.stat().st_mtime_ns, reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("review_id"):
            prepared = payload.get("prepared") or {}
            rows.append({
                "review_id": payload["review_id"],
                "status": payload.get("status"),
                "created_at": payload.get("created_at"),
                "origin": payload.get("origin"),
                "summary": payload.get("summary"),
                "files": prepared.get("files") or [],
            })
        if len(rows) >= max(1, min(int(limit), 100)):
            break
    return rows


def approve_edit_review(project_dir: str | Path, review_id: str, *, lock_timeout: float | None = 0) -> EditTransactionResult:
    root = Path(project_dir).resolve()
    path = _review_path(root, review_id)
    payload = read_edit_review(root, review_id)
    if payload.get("status") != "pending":
        raise EditEngineError("Only pending edit reviews can be approved.", path=review_id)
    raw_operations = payload.get("operations")
    if not isinstance(raw_operations, list) or not raw_operations:
        raise EditEngineError("Edit review has no operations to approve.", path=review_id)
    operations = _bind_base_hashes(
        root,
        [EditOperation.from_payload(item) for item in raw_operations],
    )
    prepared = prepare_transaction(
        root,
        operations,
        origin=str(payload.get("origin") or "review"),
        summary=str(payload.get("summary") or "Approved edit proposal"),
        policy=EditPolicy(protected_paths=_protected_paths(root)),
        validate=True,
    )
    result = commit_transaction(prepared, lock_timeout=lock_timeout)
    payload["status"] = "committed"
    payload["approved_at"] = datetime.now(timezone.utc).isoformat()
    payload["transaction_id"] = result.transaction_id
    _atomic_json(path, payload)
    return result


def reject_edit_review(project_dir: str | Path, review_id: str) -> dict[str, Any]:
    root = Path(project_dir).resolve()
    path = _review_path(root, review_id)
    payload = read_edit_review(root, review_id)
    if payload.get("status") != "pending":
        raise EditEngineError("Only pending edit reviews can be rejected.", path=review_id)
    payload["status"] = "rejected"
    payload["rejected_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_json(path, payload)
    return payload


__all__ = [
    "approve_edit_review",
    "list_edit_reviews",
    "prepare_edit_review",
    "read_edit_review",
    "reject_edit_review",
]
