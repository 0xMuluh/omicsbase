"""Compatibility façade for transactional source edits.

The stable edit API remains here while parser, matching, and transaction
implementation live under ``edit_engine_parts``.  Private helper names remain
available because recovery and review services use them directly.
"""

from __future__ import annotations

import contextlib
import difflib
import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from typing import Literal
from pathlib import Path
from typing import Any

try:  # pragma: no cover
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

from app.services.apply_edits import is_path_locked

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



def sha256_bytes(value: bytes | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

from app.services.edit_engine_parts import matching as _matching
from app.services.edit_engine_parts import parser as _parser
from app.services.edit_engine_parts import transactions as _transactions


def _safe_replace(*args: Any, **kwargs: Any):
    return _matching._safe_replace(*args, **kwargs)


def _similar_hint(*args: Any, **kwargs: Any):
    return _matching._similar_hint(*args, **kwargs)


def _cross_file_candidates(*args: Any, **kwargs: Any):
    return _matching._cross_file_candidates(*args, **kwargs)


def _replace_elision(*args: Any, **kwargs: Any):
    return _matching._replace_elision(*args, **kwargs)


def _replace_indent_flexible(*args: Any, **kwargs: Any):
    return _matching._replace_indent_flexible(*args, **kwargs)


def _replace_normalised_lines(*args: Any, **kwargs: Any):
    return _matching._replace_normalised_lines(*args, **kwargs)


def _canonical_line(*args: Any, **kwargs: Any):
    return _matching._canonical_line(*args, **kwargs)


def parse_apply_patch(patch: str):
    return _parser.parse_apply_patch(patch)


def _encode_update_patch(*args: Any, **kwargs: Any):
    return _parser._encode_update_patch(*args, **kwargs)


def _decode_hunks(*args: Any, **kwargs: Any):
    return _parser._decode_hunks(*args, **kwargs)


def _apply_hunks(*args: Any, **kwargs: Any):
    return _parser._apply_hunks(*args, **kwargs)


def safe_replace_text(*args: Any, **kwargs: Any):
    return _matching.safe_replace_text(*args, **kwargs)


def prepare_transaction(*args: Any, **kwargs: Any):
    return _transactions.prepare_transaction(*args, **kwargs)


def commit_transaction(*args: Any, **kwargs: Any):
    return _transactions.commit_transaction(*args, **kwargs)


def apply_transaction(*args: Any, **kwargs: Any):
    return _transactions.apply_transaction(*args, **kwargs)


def revert_transaction(*args: Any, **kwargs: Any):
    return _transactions.revert_transaction(*args, **kwargs)


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
