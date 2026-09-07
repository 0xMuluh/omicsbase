"""Prepare, commit, apply, and revert transactional source edits."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app.services.apply_edits import is_path_locked
from app.services.edit_engine import (
    EditConflict,
    EditEngineError,
    EditOperation,
    EditPatchError,
    EditPolicy,
    EditPolicyError,
    EditMatchError,
    EditTransactionResult,
    PreparedFile,
    PreparedTransaction,
    sha256_bytes,
)

logger = logging.getLogger(__name__)


def _compat():
    from app.services import edit_engine
    return edit_engine


def _normalise_relative_path(value: str | None):
    return _compat()._normalise_relative_path(value)


def _resolve_project_path(base: Path, relative: str | None):
    return _compat()._resolve_project_path(base, relative)


def _safe_replace(*args: Any, **kwargs: Any):
    return _compat()._safe_replace(*args, **kwargs)


def _similar_hint(*args: Any, **kwargs: Any):
    return _compat()._similar_hint(*args, **kwargs)


def _cross_file_candidates(*args: Any, **kwargs: Any):
    return _compat()._cross_file_candidates(*args, **kwargs)


def _decode_hunks(*args: Any, **kwargs: Any):
    return _compat()._decode_hunks(*args, **kwargs)


def _apply_hunks(*args: Any, **kwargs: Any):
    return _compat()._apply_hunks(*args, **kwargs)


def _project_lock(*args: Any, **kwargs: Any):
    return _compat()._project_lock(*args, **kwargs)


def _replace_bytes_atomic(*args: Any, **kwargs: Any):
    return _compat()._replace_bytes_atomic(*args, **kwargs)


def _rollback_replaced(*args: Any, **kwargs: Any):
    return _compat()._rollback_replaced(*args, **kwargs)


def _write_journal_copy(*args: Any, **kwargs: Any):
    return _compat()._write_journal_copy(*args, **kwargs)


def _read_journal_copy(*args: Any, **kwargs: Any):
    return _compat()._read_journal_copy(*args, **kwargs)


def _write_json_atomic(*args: Any, **kwargs: Any):
    return _compat()._write_json_atomic(*args, **kwargs)


def _record_pending_invalidation(*args: Any, **kwargs: Any):
    return _compat()._record_pending_invalidation(*args, **kwargs)


def parse_apply_patch(patch: str):
    return _compat().parse_apply_patch(patch)

def prepare_transaction(
    project_dir: str | Path,
    operations: Iterable[EditOperation | dict[str, Any]],
    *,
    origin: str = "agent",
    summary: str = "",
    policy: EditPolicy | None = None,
    transaction_id: str | None = None,
    validate: bool = False,
    run_r_parse: bool = False,
) -> PreparedTransaction:
    """Prepare all edits without writing project files.

    Every operation is applied to a per-file virtual working copy.  If any
    operation fails, an exception is raised and the project remains untouched.
    """

    base = Path(project_dir).resolve()
    if not base.is_dir():
        raise EditPolicyError("Project directory does not exist.")
    active_policy = policy or EditPolicy()
    parsed = [
        operation if isinstance(operation, EditOperation) else EditOperation.from_payload(operation)
        for operation in operations
    ]
    if not parsed:
        raise EditPolicyError("At least one edit operation is required.")

    transaction = PreparedTransaction(
        transaction_id=transaction_id or uuid.uuid4().hex,
        project_dir=base,
        files=[],
        operations=parsed,
        origin=origin,
        summary=summary,
    )
    original: dict[str, bytes | None] = {}
    working: dict[str, bytes | None] = {}
    modes: dict[str, int | None] = {}
    strategies: dict[str, list[str]] = {}
    reasons: dict[str, list[str]] = {}

    expanded: list[EditOperation] = []
    for operation in parsed:
        if operation.kind == "patch":
            if not operation.patch:
                raise EditPatchError("Patch operation is missing patch text.")
            patch_ops = parse_apply_patch(operation.patch)
            for patch_op in patch_ops:
                expanded.append(
                    EditOperation(
                        **{
                            **asdict(patch_op),
                            "base_sha256": operation.base_sha256,
                            "base_hashes": operation.base_hashes,
                            "reason": operation.reason,
                        }
                    )
                )
        else:
            expanded.append(operation)

    for operation in expanded:
        relative = _normalise_relative_path(operation.path)
        if relative is None:
            raise EditPolicyError("Edit paths must be relative and cannot contain traversal.", path=operation.path)
        if not active_policy.allows_path(relative):
            raise EditPolicyError("Edit path is outside the active editing policy.", path=relative)
        target = _resolve_project_path(base, relative)
        if target is None:
            raise EditPolicyError("Edit path escapes the project or traverses a symlink.", path=relative)
        if is_path_locked(base, relative):
            raise EditPolicyError("Edit path is locked.", path=relative)

        if relative not in original:
            before = target.read_bytes() if target.exists() else None
            if target.exists() and not target.is_file():
                raise EditPolicyError("Edit target is not a regular file.", path=relative)
            if before is not None and len(before) > active_policy.max_file_bytes:
                raise EditPolicyError("Edit target exceeds the configured size limit.", path=relative)
            original[relative] = before
            working[relative] = before
            modes[relative] = target.stat().st_mode & 0o777 if target.exists() else None
            strategies[relative] = []
            reasons[relative] = []

            expected = operation.base_sha256 or (operation.base_hashes or {}).get(relative)
            actual = sha256_bytes(before)
            if expected is not None and expected != actual:
                raise EditConflict(
                    "The file changed after the model read it.",
                    path=relative,
                    details={"expected_sha256": expected, "actual_sha256": actual},
                )
        elif operation.base_sha256 or operation.base_hashes:
            expected = operation.base_sha256 or (operation.base_hashes or {}).get(relative)
            actual = sha256_bytes(original[relative])
            if expected is not None and expected != actual:
                raise EditConflict(
                    "The operation base does not match the transaction snapshot.",
                    path=relative,
                    details={"expected_sha256": expected, "actual_sha256": actual},
                )

        before_current = working[relative]
        if operation.kind == "replace":
            if before_current is None:
                raise EditMatchError("SEARCH/REPLACE target does not exist.", path=relative)
            if operation.search is None or operation.replace is None:
                raise EditPolicyError("Replace operations require search and replace text.", path=relative)
            if active_policy.require_base_for_replace and not (operation.base_sha256 or (operation.base_hashes or {}).get(relative)):
                raise EditConflict("A base SHA-256 is required for this replace operation.", path=relative)
            updated, strategy, diagnostic = _safe_replace(
                before_current,
                operation.search,
                operation.replace,
                allow_multiple=operation.allow_multiple,
            )
            if updated is None:
                details = {"diagnostic": diagnostic}
                hint = _similar_hint(operation.search, before_current)
                if hint:
                    details["hint"] = hint
                candidates = _cross_file_candidates(base, operation.search, relative)
                if candidates:
                    details["cross_file_candidates"] = candidates
                raise EditMatchError(
                    diagnostic or "SEARCH block did not match exactly one location.",
                    path=relative,
                    details=details,
                )
            if updated == before_current:
                raise EditMatchError("The replace operation is a no-op.", path=relative)
            working[relative] = updated
            strategies[relative].append(strategy)
        elif operation.kind in {"rewrite", "create"}:
            content = operation.content
            if content is None:
                raise EditPolicyError("Rewrite/create operations require content.", path=relative)
            if before_current is None:
                if not active_policy.allow_create:
                    raise EditPolicyError("Creating files is disabled by the active policy.", path=relative)
            else:
                if operation.kind == "create":
                    raise EditPolicyError("Create operation targets an existing file.", path=relative)
                if not active_policy.allow_rewrite_existing:
                    raise EditPolicyError("Full rewrites of existing files are disabled.", path=relative)
                if active_policy.require_base_for_rewrite and not operation.base_sha256:
                    raise EditConflict("A base SHA-256 is required for an existing-file rewrite.", path=relative)
            encoded = content.encode("utf-8")
            if len(encoded) > active_policy.max_file_bytes:
                raise EditPolicyError("Replacement content exceeds the configured size limit.", path=relative)
            working[relative] = encoded
            strategies[relative].append("rewrite" if before_current is not None else "create")
        elif operation.kind == "delete":
            if not active_policy.allow_delete:
                raise EditPolicyError("Deleting files is disabled by the active policy.", path=relative)
            if before_current is None:
                raise EditPolicyError("Delete operation targets a missing file.", path=relative)
            working[relative] = None
            strategies[relative].append("delete")
        elif operation.kind == "patch_hunks":
            if before_current is None:
                raise EditMatchError("Patch target does not exist.", path=relative)
            if not operation.patch:
                raise EditPatchError("Patch hunk operation is missing encoded hunks.", path=relative)
            hunks, require_eof, no_final_newline = _decode_hunks(operation.patch)
            updated = _apply_hunks(
                before_current,
                hunks,
                path=relative,
                require_eof=require_eof,
                no_final_newline=no_final_newline,
            )
            if updated == before_current:
                raise EditMatchError("The patch operation is a no-op.", path=relative)
            working[relative] = updated
            strategies[relative].append("patch")
        else:  # pragma: no cover - parser guarantees the union
            raise EditPolicyError(f"Unsupported edit kind: {operation.kind}", path=relative)
        if operation.reason:
            reasons[relative].append(operation.reason)

    for relative, before in original.items():
        after = working[relative]
        if before == after:
            raise EditMatchError("The transaction contains no material file changes.", path=relative)
        transaction.files.append(
            PreparedFile(
                path=relative,
                before=before,
                after=after,
                before_sha256=sha256_bytes(before),
                after_sha256=sha256_bytes(after),
                mode=modes[relative],
                strategies=strategies[relative],
                reasons=reasons[relative],
            )
        )
    if not transaction.files:
        raise EditMatchError("The transaction contains no material file changes.")
    if validate:
        from app.services.edit_validation import validate_prepared_transaction

        validation = validate_prepared_transaction(transaction, run_r_parse=run_r_parse)
        transaction.diagnostics.extend(issue.as_dict() for issue in validation.issues)
        if not validation.valid:
            raise EditPolicyError(
                "Scientific validation rejected the prepared edit.",
                details=validation.as_dict(),
            )
    return transaction


def commit_transaction(
    prepared: PreparedTransaction,
    *,
    lock_timeout: float | None = None,
) -> EditTransactionResult:
    """Commit a prepared transaction with hash rechecking and recovery journal."""

    base = prepared.project_dir.resolve()
    journal_dir = base / ".omicsbase" / "edits" / prepared.transaction_id
    journal_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = journal_dir / "manifest.json"
    manifest = {
        "transaction_id": prepared.transaction_id,
        "origin": prepared.origin,
        "summary": prepared.summary,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "prepared",
        "files": [item.to_dict(include_diff=False) for item in prepared.files],
        "diagnostics": list(prepared.diagnostics),
    }
    _write_json_atomic(manifest_path, manifest)

    with _project_lock(base, timeout=lock_timeout):
        for item in prepared.files:
            target = _resolve_project_path(base, item.path)
            if target is None:
                raise EditPolicyError("Prepared path is no longer inside the project.", path=item.path)
            if is_path_locked(base, item.path):
                raise EditPolicyError("Edit path was locked after preparation.", path=item.path)
            current = target.read_bytes() if target.exists() else None
            actual = sha256_bytes(current)
            if actual != item.before_sha256:
                raise EditConflict(
                    "The file changed before the edit could be committed.",
                    path=item.path,
                    details={"expected_sha256": item.before_sha256, "actual_sha256": actual},
                )
            _write_journal_copy(journal_dir / "before", item.path, current)
            _write_journal_copy(journal_dir / "after", item.path, item.after)

        manifest["status"] = "committing"
        _write_json_atomic(manifest_path, manifest)
        replaced: list[PreparedFile] = []
        try:
            for item in sorted(prepared.files, key=lambda value: value.path):
                target = _resolve_project_path(base, item.path)
                assert target is not None
                if item.after is None:
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _replace_bytes_atomic(target, item.after, mode=item.mode)
                replaced.append(item)
        except Exception as exc:
            _rollback_replaced(base, journal_dir / "before", replaced)
            manifest["status"] = "rolled_back"
            manifest["error"] = str(exc)
            _write_json_atomic(manifest_path, manifest)
            raise EditEngineError("Edit commit failed and was rolled back.", details={"error": str(exc)}) from exc

        manifest["status"] = "committed"
        try:
            manifest["invalidation"] = _record_pending_invalidation(
                base,
                [item.path for item in prepared.files],
            )
        except Exception as exc:  # source commit must not be rolled back after bytes are replaced
            logger.warning("Could not record pending invalidation for %s: %s", prepared.transaction_id, exc)
            manifest["invalidation_error"] = str(exc)[:500]
        _write_json_atomic(manifest_path, manifest)
    prepared.status = "committed"
    return EditTransactionResult(
        transaction_id=prepared.transaction_id,
        status="committed",
        files=prepared.files,
        origin=prepared.origin,
        summary=prepared.summary,
        diagnostics=list(prepared.diagnostics),
        journal_dir=str(journal_dir),
    )


def apply_transaction(
    project_dir: str | Path,
    operations: Iterable[EditOperation | dict[str, Any]],
    *,
    origin: str = "agent",
    summary: str = "",
    policy: EditPolicy | None = None,
    lock_timeout: float | None = None,
    validate: bool = False,
    run_r_parse: bool = False,
) -> EditTransactionResult:
    prepared = prepare_transaction(
        project_dir,
        operations,
        origin=origin,
        summary=summary,
        policy=policy,
        validate=validate,
        run_r_parse=run_r_parse,
    )
    return commit_transaction(prepared, lock_timeout=lock_timeout)


def revert_transaction(project_dir: str | Path, transaction_id: str, *, lock_timeout: float | None = None) -> EditTransactionResult:
    """Revert a committed transaction only when no later change touched it."""

    base = Path(project_dir).resolve()
    journal_dir = base / ".omicsbase" / "edits" / transaction_id
    manifest_path = journal_dir / "manifest.json"
    if not manifest_path.is_file():
        raise EditPolicyError("Edit transaction journal was not found.", path=transaction_id)
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("status") != "committed":
        raise EditPolicyError("Only committed edit transactions can be reverted.", path=transaction_id)

    operations: list[EditOperation] = []
    for item in manifest.get("files") or []:
        path = str(item.get("path") or "")
        after = _read_journal_copy(journal_dir / "after", path)
        before = _read_journal_copy(journal_dir / "before", path)
        current_path = _resolve_project_path(base, path)
        current = current_path.read_bytes() if current_path and current_path.exists() else None
        if sha256_bytes(current) != item.get("after_sha256"):
            raise EditConflict("The file changed after this transaction; refusing to overwrite it.", path=path)
        if before is None:
            operations.append(EditOperation(path=path, kind="delete", base_sha256=item.get("after_sha256")))
        else:
            operations.append(
                EditOperation(
                    path=path,
                    kind="rewrite",
                    content=before.decode("utf-8"),
                    base_sha256=item.get("after_sha256"),
                )
            )
        # Keep the after copy read above as an explicit integrity check.
        if after is not None and sha256_bytes(after) != item.get("after_sha256"):
            raise EditEngineError("Edit journal after-copy hash is corrupt.", path=path)

    result = apply_transaction(
        base,
        operations,
        origin="revert",
        summary=f"Revert edit transaction {transaction_id}",
        policy=EditPolicy(allow_delete=True),
        lock_timeout=lock_timeout,
    )
    manifest["reverted_by"] = result.transaction_id
    manifest["status"] = "reverted"
    _write_json_atomic(manifest_path, manifest)
    return result


