"""Transactional source-edit engine for OmicsBase projects.

The model is allowed to choose an edit representation, but no representation is
allowed to write directly to a project.  This module prepares every operation
against an in-memory working tree, verifies the base hashes, and commits staged
files under the same cross-process lock used by the project runner.

The engine deliberately keeps the matching ladder conservative.  Similarity
matching is useful for diagnostics, but a scientific source edit must have one
unambiguous target before it is committed.
"""

from __future__ import annotations

import contextlib
import difflib
import hashlib
import json
import logging
import os
import re
import tempfile
import unicodedata
import uuid
from datetime import datetime, timezone
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

try:  # pragma: no cover - Windows fallback is exercised through the no-op path
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

from app.services.apply_edits import is_path_locked
from app.services.fuzzy_replace import find_similar_lines

logger = logging.getLogger(__name__)


EditKind = Literal["replace", "rewrite", "create", "delete", "patch", "patch_hunks"]

DEFAULT_TEXT_EXTENSIONS = frozenset(
    {
        ".r",
        ".qmd",
        ".md",
        ".yml",
        ".yaml",
        ".json",
        ".txt",
        ".csv",
        ".tsv",
        ".html",
        ".css",
        ".js",
        ".ts",
        ".tsx",
    }
)


class EditEngineError(Exception):
    """Base error with a stable machine-readable code and diagnostics."""

    code = "edit_error"

    def __init__(self, message: str, *, path: str | None = None, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.path = path
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        payload = {"code": self.code, "message": str(self)}
        if self.path:
            payload["path"] = self.path
        if self.details:
            payload["details"] = self.details
        return payload


class EditPolicyError(EditEngineError):
    code = "policy_error"


class EditConflict(EditEngineError):
    code = "edit_conflict"


class EditBusy(EditEngineError):
    code = "edit_busy"


class EditMatchError(EditEngineError):
    code = "match_error"


class EditPatchError(EditEngineError):
    code = "patch_error"


@dataclass(frozen=True)
class EditPolicy:
    """Policy applied before any source bytes are changed."""

    allowed_extensions: frozenset[str] = DEFAULT_TEXT_EXTENSIONS
    protected_paths: frozenset[str] = frozenset()
    excluded_prefixes: tuple[str, ...] = (".omicsbase",)
    allow_create: bool = True
    allow_delete: bool = False
    allow_rewrite_existing: bool = True
    require_base_for_replace: bool = False
    require_base_for_rewrite: bool = True
    max_file_bytes: int = 2_000_000

    def allows_path(self, relative_path: str) -> bool:
        normalized = _normalise_relative_path(relative_path)
        if normalized is None:
            return False
        if self.allowed_extensions and Path(normalized).suffix.lower() not in {
            item.lower() for item in self.allowed_extensions
        }:
            return False
        if any(
            normalized == prefix or normalized.startswith(prefix.rstrip("/") + "/")
            for prefix in self.excluded_prefixes
        ):
            return False
        if any(
            normalized == prefix or normalized.startswith(prefix.rstrip("/") + "/")
            for prefix in self.protected_paths
        ):
            return False
        return True


@dataclass(frozen=True)
class EditOperation:
    path: str | None = None
    kind: EditKind = "replace"
    search: str | None = None
    replace: str | None = None
    content: str | None = None
    patch: str | None = None
    base_sha256: str | None = None
    base_hashes: dict[str, str] | None = None
    allow_multiple: bool = False
    reason: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "EditOperation":
        if not isinstance(payload, dict):
            raise EditPolicyError("Each edit operation must be an object.")
        kind = str(payload.get("kind") or "").strip().lower()
        if not kind:
            if isinstance(payload.get("patch"), str):
                kind = "patch"
            elif "search" in payload or "replace" in payload:
                kind = "replace"
            elif "content" in payload:
                kind = "rewrite"
        if kind not in {"replace", "rewrite", "create", "delete", "patch", "patch_hunks"}:
            raise EditPolicyError(f"Unsupported edit operation kind: {kind or '(missing)'}")
        path = payload.get("path")
        if path is not None:
            path = str(path).strip()
        return cls(
            path=path or None,
            kind=kind,  # type: ignore[arg-type]
            search=payload.get("search") if isinstance(payload.get("search"), str) else None,
            replace=payload.get("replace") if isinstance(payload.get("replace"), str) else None,
            content=payload.get("content") if isinstance(payload.get("content"), str) else None,
            patch=payload.get("patch") if isinstance(payload.get("patch"), str) else None,
            base_sha256=str(payload["base_sha256"]) if payload.get("base_sha256") else None,
            base_hashes=(
                {str(key): str(value) for key, value in payload["base_hashes"].items()}
                if isinstance(payload.get("base_hashes"), dict)
                else None
            ),
            allow_multiple=bool(payload.get("allow_multiple", False)),
            reason=str(payload.get("reason"))[:1000] if payload.get("reason") else None,
        )


@dataclass
class PreparedFile:
    path: str
    before: bytes | None
    after: bytes | None
    before_sha256: str | None
    after_sha256: str | None
    mode: int | None
    strategies: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self, *, include_diff: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "path": self.path,
            "before_sha256": self.before_sha256,
            "after_sha256": self.after_sha256,
            "strategies": list(self.strategies),
            "reasons": list(self.reasons),
        }
        if include_diff and self.before is not None and self.after is not None:
            try:
                before_text = self.before.decode("utf-8")
                after_text = self.after.decode("utf-8")
            except UnicodeDecodeError:
                pass
            else:
                payload["diff"] = "".join(
                    difflib.unified_diff(
                        before_text.splitlines(keepends=True),
                        after_text.splitlines(keepends=True),
                        fromfile=f"a/{self.path}",
                        tofile=f"b/{self.path}",
                        n=3,
                    )
                )
        return payload


@dataclass
class PreparedTransaction:
    transaction_id: str
    project_dir: Path
    files: list[PreparedFile]
    operations: list[EditOperation]
    origin: str = "agent"
    summary: str = ""
    status: str = "prepared"
    diagnostics: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self, *, include_diff: bool = True) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "status": self.status,
            "origin": self.origin,
            "summary": self.summary,
            "files": [item.to_dict(include_diff=include_diff) for item in self.files],
            "diagnostics": list(self.diagnostics),
        }


@dataclass
class EditTransactionResult:
    transaction_id: str
    status: str
    files: list[PreparedFile] = field(default_factory=list)
    origin: str = "agent"
    summary: str = ""
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    journal_dir: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "status": self.status,
            "origin": self.origin,
            "summary": self.summary,
            "modified_files": [item.path for item in self.files],
            "files": [item.to_dict() for item in self.files],
            "diagnostics": list(self.diagnostics),
            "journal_dir": self.journal_dir,
        }


def sha256_bytes(value: bytes | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
            hunks, require_eof = _decode_hunks(operation.patch)
            updated = _apply_hunks(before_current, hunks, path=relative, require_eof=require_eof)
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


@dataclass(frozen=True)
class _PatchSpec:
    kind: Literal["create", "delete", "update"]
    path: str
    lines: tuple[str, ...] = ()
    hunks: tuple[tuple[str, ...], ...] = ()
    eof: bool = False


def parse_apply_patch(patch: str) -> list[EditOperation]:
    """Parse the Codex-style ``*** Begin Patch`` envelope into operations."""

    lines = patch.splitlines()
    if not lines or lines[0].strip() != "*** Begin Patch":
        raise EditPatchError("Patch must start with *** Begin Patch.")
    if "*** End Patch" not in lines:
        raise EditPatchError("Patch must end with *** End Patch.")
    end = lines.index("*** End Patch")
    specs: list[_PatchSpec] = []
    i = 1
    while i < end:
        line = lines[i]
        if line.startswith("*** Add File: "):
            path = line.removeprefix("*** Add File: ").strip()
            i += 1
            added: list[str] = []
            while i < end and not lines[i].startswith("*** "):
                if not lines[i].startswith("+"):
                    raise EditPatchError("Added-file patch lines must begin with +.", path=path)
                added.append(lines[i][1:])
                i += 1
            specs.append(_PatchSpec("create", path, lines=tuple(added)))
            continue
        if line.startswith("*** Delete File: "):
            specs.append(_PatchSpec("delete", line.removeprefix("*** Delete File: ").strip()))
            i += 1
            continue
        if line.startswith("*** Update File: "):
            path = line.removeprefix("*** Update File: ").strip()
            i += 1
            hunks: list[tuple[str, ...]] = []
            current: list[str] = []
            eof = False
            while i < end and not lines[i].startswith("*** "):
                if lines[i].startswith("@@"):
                    if current:
                        hunks.append(tuple(current))
                        current = []
                    i += 1
                    continue
                if lines[i] == "\\ No newline at end of file":
                    i += 1
                    continue
                if not lines[i] or lines[i][0] not in {" ", "+", "-"}:
                    raise EditPatchError("Malformed patch hunk line.", path=path)
                current.append(lines[i])
                i += 1
            if current:
                hunks.append(tuple(current))
            if i < end and lines[i] == "*** End of File":
                eof = True
                i += 1
            if not hunks:
                raise EditPatchError("Update patch has no hunks.", path=path)
            specs.append(_PatchSpec("update", path, hunks=tuple(hunks), eof=eof))
            continue
        raise EditPatchError(f"Unexpected patch line: {line}")

    operations: list[EditOperation] = []
    for spec in specs:
        if spec.kind == "create":
            operations.append(EditOperation(path=spec.path, kind="create", content="\n".join(spec.lines) + ("\n" if spec.lines else "")))
        elif spec.kind == "delete":
            operations.append(EditOperation(path=spec.path, kind="delete"))
        else:
            operations.append(EditOperation(path=spec.path, kind="patch_hunks", patch=_encode_update_patch(spec)))
    return operations


def _encode_update_patch(spec: _PatchSpec) -> str:
    return json.dumps({"path": spec.path, "hunks": [list(hunk) for hunk in spec.hunks], "eof": spec.eof})


def _decode_hunks(value: str) -> tuple[tuple[tuple[str, ...], ...], bool]:
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise EditPatchError("Encoded patch hunks are invalid.") from exc
    hunks = payload.get("hunks") if isinstance(payload, dict) else None
    if not isinstance(hunks, list) or not hunks or any(not isinstance(hunk, list) or any(not isinstance(line, str) for line in hunk) for hunk in hunks):
        raise EditPatchError("Encoded patch hunks are invalid.")
    return tuple(tuple(hunk) for hunk in hunks), bool(payload.get("eof", False))


def _apply_hunks(whole: bytes, hunks: tuple[tuple[str, ...], ...], *, path: str, require_eof: bool = False) -> bytes:
    try:
        text = whole.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EditPatchError("Patch target is not valid UTF-8.", path=path) from exc
    had_final_newline = text.endswith("\n")
    lines = text.splitlines()
    for hunk in hunks:
        old_lines = [line[1:] for line in hunk if line and line[0] in {" ", "-"}]
        new_lines = [line[1:] for line in hunk if line and line[0] in {" ", "+"}]
        if not old_lines:
            lines.extend(new_lines)
            continue
        candidates = [index for index in range(len(lines) - len(old_lines) + 1) if lines[index : index + len(old_lines)] == old_lines]
        if len(candidates) != 1:
            raise EditMatchError("Patch hunk context did not match exactly one location.", path=path, details={"matches": len(candidates)})
        start = candidates[0]
        if require_eof and start + len(old_lines) != len(lines):
            raise EditMatchError("Patch hunk marked End of File but its context is not at EOF.", path=path)
        lines[start : start + len(old_lines)] = new_lines
    updated = "\n".join(lines)
    if had_final_newline:
        updated += "\n"
    return updated.encode("utf-8")


def _safe_replace(whole: bytes, search: str, replace: str, *, allow_multiple: bool) -> tuple[bytes | None, str, str | None]:
    try:
        text = whole.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, "none", f"Target is not valid UTF-8: {exc}"
    if not search:
        updated = text + (("\n" if text and not text.endswith("\n") else "") + replace)
        return updated.encode("utf-8"), "append", None
    count = text.count(search)
    if count > 1 and not allow_multiple:
        return None, "ambiguous", f"SEARCH occurs {count} times; provide a larger unique block or allow_multiple."
    if count > 1 and allow_multiple:
        return text.replace(search, replace).encode("utf-8"), "exact_multiple", None
    if count == 1:
        return text.replace(search, replace, 1).encode("utf-8"), "exact", None

    normalised = _replace_normalised_lines(text, search, replace)
    if normalised is not None:
        return normalised.encode("utf-8"), "unicode_normalized", None
    indented = _replace_indent_flexible(text, search, replace)
    if indented is not None:
        return indented.encode("utf-8"), "indent_flexible", None
    elided = _replace_elision(text, search, replace)
    if elided is not None:
        return elided.encode("utf-8"), "elision_anchor", None
    return None, "none", "SEARCH block failed to match exactly one location."


def safe_replace_text(whole: str, search: str, replace: str, *, allow_multiple: bool = False) -> tuple[bool, str, str, str | None]:
    """Apply the engine matcher without touching a project on disk.

    Adapters that need to validate a generated response before a filesystem
    transaction use this helper instead of the legacy first-match utility.
    The returned strategy and diagnostic are suitable for reflection prompts.
    """
    updated, strategy, diagnostic = _safe_replace(
        whole.encode("utf-8"), search, replace, allow_multiple=allow_multiple
    )
    if updated is None:
        return False, whole, strategy, diagnostic
    return True, updated.decode("utf-8"), strategy, None


def _replace_elision(whole: str, search: str, replace: str) -> str | None:
    """Apply a single explicit ``...`` line as a conservative gap anchor."""
    source_lines = whole.splitlines(keepends=True)
    wanted = search.splitlines()
    markers = [index for index, line in enumerate(wanted) if line.strip() in {"...", "…"}]
    if len(markers) != 1:
        return None
    marker = markers[0]
    prefix = wanted[:marker]
    suffix = wanted[marker + 1:]
    if not prefix or not suffix:
        return None
    def matches(start: int, expected: list[str]) -> bool:
        if start < 0 or start + len(expected) > len(source_lines):
            return False
        return [_canonical_line(line.rstrip("\r\n")) for line in source_lines[start:start + len(expected)]] == [_canonical_line(line) for line in expected]
    candidates: list[tuple[int, int]] = []
    for start in range(len(source_lines) - len(prefix) + 1):
        if not matches(start, prefix):
            continue
        suffix_start = start + len(prefix)
        for end in range(suffix_start + 1, len(source_lines) - len(suffix) + 1):
            if matches(end, suffix):
                candidates.append((start, end + len(suffix)))
    if len(candidates) != 1:
        return None
    start, end = candidates[0]
    replacement = replace.splitlines(keepends=True)
    return "".join(source_lines[:start] + replacement + source_lines[end:])


def _cross_file_candidates(base: Path, search: str, excluded: str) -> list[str]:
    """Report exact/canonical matches in sibling source files for reflection."""
    if not search.strip():
        return []
    candidates: list[str] = []
    for path in sorted(base.rglob("*")):
        if len(candidates) >= 5 or not path.is_file():
            continue
        relative = path.relative_to(base).as_posix()
        if relative == excluded or relative.startswith(".omicsbase/"):
            continue
        if path.suffix.lower() not in DEFAULT_TEXT_EXTENSIONS:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if search in text or _replace_normalised_lines(text, search, "") is not None:
            candidates.append(relative)
    return candidates


def _replace_indent_flexible(whole: str, search: str, replace: str) -> str | None:
    whole_lines = whole.splitlines(keepends=True)
    search_lines = search.splitlines()
    if not search_lines or len(search_lines) > len(whole_lines):
        return None
    candidates: list[int] = []
    for index in range(len(whole_lines) - len(search_lines) + 1):
        actual = [line.rstrip("\r\n") for line in whole_lines[index : index + len(search_lines)]]
        wanted = [line.rstrip("\r\n") for line in search_lines]
        if not all(_canonical_line(left).lstrip() == _canonical_line(right).lstrip() for left, right in zip(actual, wanted)):
            continue
        deltas = [len(left) - len(left.lstrip()) - (len(right) - len(right.lstrip())) for left, right in zip(actual, wanted) if right.strip()]
        if deltas and len(set(deltas)) == 1:
            candidates.append(index)
    if len(candidates) != 1:
        return None
    index = candidates[0]
    delta = next((len(whole_lines[index + offset]) - len(whole_lines[index + offset].lstrip()) - (len(search_lines[offset]) - len(search_lines[offset].lstrip())) for offset in range(len(search_lines)) if search_lines[offset].strip()), 0)
    replacement_lines: list[str] = []
    for line in replace.splitlines(keepends=True):
        if line.strip():
            leading = len(line) - len(line.lstrip())
            replacement_lines.append(" " * max(0, leading + delta) + line.lstrip())
        else:
            replacement_lines.append(line)
    return "".join(whole_lines[:index] + replacement_lines + whole_lines[index + len(search_lines) :])


def _replace_normalised_lines(whole: str, search: str, replace: str) -> str | None:
    whole_lines = whole.splitlines(keepends=True)
    search_lines = search.splitlines()
    if not search_lines:
        return None
    wanted = [_canonical_line(item) for item in search_lines]
    candidates: list[int] = []
    for index in range(len(whole_lines) - len(wanted) + 1):
        actual = [_canonical_line(item) for item in whole_lines[index : index + len(wanted)]]
        if actual == wanted:
            candidates.append(index)
    if len(candidates) != 1:
        return None
    index = candidates[0]
    replacement_lines = replace.splitlines(keepends=True)
    if replace and replacement_lines and not replacement_lines[-1].endswith("\n") and whole_lines[index : index + len(wanted)][-1].endswith("\n"):
        replacement_lines[-1] += "\n"
    return "".join(whole_lines[:index] + replacement_lines + whole_lines[index + len(wanted) :])


def _canonical_line(value: str) -> str:
    translated = value.translate(
        str.maketrans(
            {
                "\u2018": "'",
                "\u2019": "'",
                "\u201c": '"',
                "\u201d": '"',
                "\u2013": "-",
                "\u2014": "-",
                "\u2212": "-",
                "\u00a0": " ",
            }
        )
    )
    return unicodedata.normalize("NFKC", translated).rstrip()


def _similar_hint(search: str, before: bytes) -> str:
    try:
        return find_similar_lines(search, before.decode("utf-8"))
    except UnicodeDecodeError:
        return ""


def _normalise_relative_path(value: str | None) -> str | None:
    if value is None:
        return None
    raw = str(value).replace("\\", "/").strip()
    if not raw or raw.startswith("/"):
        return None
    path = Path(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return "/".join(path.parts)


def _resolve_project_path(base: Path, relative: str | None) -> Path | None:
    normalized = _normalise_relative_path(relative)
    if normalized is None:
        return None
    candidate = (base / normalized).resolve(strict=False)
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate


@contextlib.contextmanager
def _project_lock(base: Path, *, timeout: float | None = None):
    """Use the runner's cross-process lock for source mutations as well."""

    lock_dir = base / ".omicsbase"
    lock_dir.mkdir(parents=True, exist_ok=True)
    handle = (lock_dir / "execution.lock").open("a+")
    try:
        if fcntl is not None:
            flags = fcntl.LOCK_EX
            if timeout is not None:
                flags |= fcntl.LOCK_NB
                deadline = __import__("time").monotonic() + timeout
                while True:
                    try:
                        fcntl.flock(handle.fileno(), flags)
                        break
                    except BlockingIOError:
                        if __import__("time").monotonic() >= deadline:
                            raise EditBusy("The project is busy with another edit or render.")
                        __import__("time").sleep(0.05)
            else:
                fcntl.flock(handle.fileno(), flags)
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _replace_bytes_atomic(target: Path, content: bytes, *, mode: int | None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.omicsbase-", dir=str(target.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _rollback_replaced(base: Path, before_dir: Path, replaced: list[PreparedFile]) -> None:
    for item in reversed(replaced):
        target = _resolve_project_path(base, item.path)
        if target is None:
            continue
        previous = _read_journal_copy(before_dir, item.path)
        try:
            if previous is None:
                target.unlink(missing_ok=True)
            else:
                _replace_bytes_atomic(target, previous, mode=item.mode)
        except Exception:
            # The journal remains available for startup/manual recovery.
            pass


def _write_journal_copy(root: Path, relative: str, content: bytes | None) -> None:
    target = root / relative
    if content is None:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def _read_journal_copy(root: Path, relative: str) -> bytes | None:
    target = root / relative
    return target.read_bytes() if target.is_file() else None


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    _replace_bytes_atomic(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"), mode=0o600)


def _record_pending_invalidation(base: Path, paths: list[str]) -> dict[str, Any]:
    """Persist a conservative rerun boundary for the next project execution.

    A materialized ReportPack snapshot is the richest dependency map available
    at edit time, so use it when valid. Older projects may only have the
    generated execution contract (or neither artifact); those projects retain
    the conservative legacy boundary. Runner-side loading remains defensive
    and treats this file as advisory metadata, never as permission to execute
    an unsafe path.
    """

    target = base / ".omicsbase" / "invalidation.json"
    prior_paths: list[str] = []
    try:
        existing = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(existing, dict) and isinstance(existing.get("changed_paths"), list):
            prior_paths = [str(path) for path in existing["changed_paths"] if str(path).strip()]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        pass
    changed_paths = sorted({
        str(path).replace("\\", "/")
        for path in [*prior_paths, *paths]
        if str(path).strip()
    })
    payload: dict[str, Any] = {
        "changed_paths": changed_paths,
        "impacted_capabilities": [],
        "resume_from_step": None,
        "invalidated_steps": [],
        "earliest_step_index": None,
        "targeted_pages": [],
        "full_workflow_invalidated": False,
        "source": "legacy",
    }

    # Generated ReportPack projects carry a strict manifest snapshot. Let the
    # same planner used by capability validation compute capability and page
    # impact, while retaining the accumulated changed-path set across edits.
    try:
        from app.services.incremental_invalidation import plan_invalidation
        from app.services.report_pack import load_report_pack

        pack = load_report_pack(base, manifest_name="report_pack.yaml")
        if pack.source == "declared" and (pack.execution is not None or pack.capabilities):
            plan = plan_invalidation(pack, changed_paths)
            payload.update(plan.as_dict())
            payload["source"] = "report_pack"
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_json_atomic(target, payload)
            return payload
    except (OSError, UnicodeDecodeError, ValueError, TypeError):
        # A malformed/partial snapshot must not make a committed edit fail.
        # Fall through to the validated execution-contract compatibility path.
        logger.debug("ReportPack invalidation planning unavailable for %s", base, exc_info=True)

    contract_path = base / "execution_contract.json"
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        steps = list(contract.get("steps") or [])
        working_directory = str(contract.get("working_directory") or "").strip().replace("\\", "/")
        targeted_pages: list[str] = []
        for relative in changed_paths:
            if Path(relative).suffix.lower() not in {".qmd", ".rmd"}:
                continue
            page = relative
            if working_directory and page.startswith(working_directory.rstrip("/") + "/"):
                page = page[len(working_directory.rstrip("/")) + 1:]
            elif page.startswith("code/"):
                page = page[5:]
            targeted_pages.append(page)
        payload["targeted_pages"] = sorted(set(targeted_pages))
        if isinstance(steps, list):
            indexes: list[int] = []
            for relative in changed_paths:
                for index, step in enumerate(steps):
                    if isinstance(step, dict) and step.get("path") == relative:
                        indexes.append(index)
                        break
                else:
                    # An undeclared R helper can affect the first executable
                    # step. QMD/RMD changes are render-only unless explicitly
                    # represented as an execution step.
                    if Path(relative).suffix.lower() == ".r" and steps:
                        indexes.append(0)
            if indexes:
                earliest = min(indexes)
                affected = [
                    step.get("id")
                    for step in steps[earliest:]
                    if isinstance(step, dict) and step.get("id")
                ]
                payload["resume_from_step"] = steps[earliest].get("id") if isinstance(steps[earliest], dict) else None
                payload["invalidated_steps"] = affected
                payload["earliest_step_index"] = earliest
                payload["full_workflow_invalidated"] = earliest == 0 and bool(affected)
        payload["source"] = "execution_contract"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, AttributeError):
        # Projects without an execution contract still get a durable changed
        # path record; they simply have no step-level resume boundary.
        pass
    target.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(target, payload)
    return payload


__all__ = [
    "EditBusy",
    "EditConflict",
    "EditEngineError",
    "EditKind",
    "EditMatchError",
    "EditOperation",
    "EditPatchError",
    "EditPolicy",
    "EditPolicyError",
    "EditTransactionResult",
    "PreparedFile",
    "PreparedTransaction",
    "apply_transaction",
    "commit_transaction",
    "parse_apply_patch",
    "prepare_transaction",
    "revert_transaction",
    "sha256_bytes",
    "sha256_file",
    "safe_replace_text",
]
