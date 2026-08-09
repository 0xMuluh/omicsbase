"""Atomic, content-addressed checkpoints for resumable source generation.

The checkpoint distinguishes generator-owned bytes from user/agent edits.  A
completed unit may be reused only while both its input fingerprint and every
recorded output hash still match.  Files whose bytes diverge from the last
generator-owned hash are preserved instead of being overwritten.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


CHECKPOINT_SCHEMA_VERSION = 1
CHECKPOINT_RELATIVE_PATH = Path(".omicsbase") / "generation_checkpoint.json"


def canonical_sha256(value: Any) -> str:
    """Hash a JSON-compatible value with stable ordering and separators."""
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: Path) -> str | None:
    """Return a file digest, or ``None`` when the path is absent/non-file."""
    if not path.is_file() or path.is_symlink():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_relative(path: str | Path) -> str:
    raw = str(path).replace("\\", "/")
    relative = PurePosixPath(raw)
    if not raw or relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Checkpoint output must be a safe relative path: {path!r}")
    return relative.as_posix()


@dataclass(frozen=True)
class CheckpointDecision:
    """Decision for one generation unit."""

    action: str  # run | skip | preserve
    reason: str
    record: dict[str, Any] | None = None


class GenerationCheckpoint:
    """Persist per-unit source ownership and resumability state."""

    def __init__(
        self,
        project_dir: str | Path,
        *,
        run_inputs: dict[str, Any],
        generator_version: str,
        resume: bool = True,
    ) -> None:
        self.base = Path(project_dir).resolve()
        self.path = self.base / CHECKPOINT_RELATIVE_PATH
        self.generator_version = generator_version
        self.run_inputs = run_inputs
        self.run_fingerprint = canonical_sha256(run_inputs)
        loaded = self._load() if resume else {}
        units = loaded.get("units") if isinstance(loaded.get("units"), dict) else {}
        files = loaded.get("files") if isinstance(loaded.get("files"), dict) else {}
        self.state: dict[str, Any] = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "generator_version": generator_version,
            "run_fingerprint": self.run_fingerprint,
            "input_components": run_inputs,
            "status": "running",
            # Keep old unit/file ownership records across an input change. They
            # tell us whether existing bytes are safe generator output or a
            # divergent edit that must be preserved.
            "units": units,
            "files": files,
            "updated_at": _utc_now(),
        }
        self._write()

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != CHECKPOINT_SCHEMA_VERSION
        ):
            return {}
        return value

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.state["updated_at"] = _utc_now()
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".generation-checkpoint-",
            suffix=".tmp",
            dir=str(self.path.parent),
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(self.state, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def _unit_fingerprint(self, unit_id: str, unit_inputs: Any = None) -> str:
        return canonical_sha256(
            {
                "run_fingerprint": self.run_fingerprint,
                "unit_id": unit_id,
                "unit_inputs": unit_inputs,
            }
        )

    def _actual_outputs(self, paths: Iterable[str | Path]) -> dict[str, str | None]:
        return {
            (relative := _safe_relative(path)): file_sha256(self.base / relative)
            for path in paths
        }

    def _record_outputs_match(
        self,
        record: dict[str, Any],
        actual: dict[str, str | None],
    ) -> bool:
        expected = record.get("outputs")
        return isinstance(expected, dict) and expected == actual

    def _owned_by_current_run(self, actual: dict[str, str | None]) -> bool:
        if not actual:
            return False
        ownership = self.state["files"]
        for relative, digest in actual.items():
            owner = ownership.get(relative)
            if (
                not isinstance(owner, dict)
                or owner.get("run_fingerprint") != self.run_fingerprint
                or owner.get("sha256") != digest
            ):
                return False
        return True

    def _has_divergent_existing_output(
        self,
        actual: dict[str, str | None],
    ) -> bool:
        ownership = self.state["files"]
        for relative, digest in actual.items():
            if digest is None:
                continue
            owner = ownership.get(relative)
            if not isinstance(owner, dict) or owner.get("sha256") != digest:
                return True
        return False

    def decide(
        self,
        unit_id: str,
        paths: Iterable[str | Path],
        *,
        unit_inputs: Any = None,
    ) -> CheckpointDecision:
        """Return whether a unit should run, be reused, or preserve edits."""
        safe_paths = tuple(_safe_relative(path) for path in paths)
        actual = self._actual_outputs(safe_paths)
        fingerprint = self._unit_fingerprint(unit_id, unit_inputs)
        record = self.state["units"].get(unit_id)
        if (
            isinstance(record, dict)
            and record.get("fingerprint") == fingerprint
            and record.get("status") in {"completed", "preserved"}
        ):
            if self._record_outputs_match(record, actual):
                return CheckpointDecision("skip", "matching checkpoint", record)
            # A downstream unit (adaptation or QA) may have legitimately
            # superseded this unit's bytes during the same run.
            if self._owned_by_current_run(actual):
                return CheckpointDecision(
                    "skip",
                    "output superseded by a completed downstream unit",
                    record,
                )
            return CheckpointDecision(
                "preserve",
                "output diverged from the recorded generator-owned bytes",
                record,
            )
        if self._has_divergent_existing_output(actual):
            return CheckpointDecision(
                "preserve",
                "existing output is unknown or was edited after generation",
                record if isinstance(record, dict) else None,
            )
        return CheckpointDecision("run", "unit is missing, failed, or stale", record)

    def complete(
        self,
        unit_id: str,
        paths: Iterable[str | Path],
        *,
        unit_inputs: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        safe_paths = tuple(_safe_relative(path) for path in paths)
        outputs = self._actual_outputs(safe_paths)
        record = {
            "fingerprint": self._unit_fingerprint(unit_id, unit_inputs),
            "status": "completed",
            "outputs": outputs,
            "metadata": metadata or {},
            "updated_at": _utc_now(),
        }
        self.state["units"][unit_id] = record
        for relative, digest in outputs.items():
            self.state["files"][relative] = {
                "sha256": digest,
                "unit_id": unit_id,
                "run_fingerprint": self.run_fingerprint,
                "updated_at": record["updated_at"],
            }
        self._write()
        return record

    def preserve(
        self,
        unit_id: str,
        paths: Iterable[str | Path],
        *,
        reason: str,
        unit_inputs: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        safe_paths = tuple(_safe_relative(path) for path in paths)
        record = {
            "fingerprint": self._unit_fingerprint(unit_id, unit_inputs),
            "status": "preserved",
            "outputs": self._actual_outputs(safe_paths),
            "metadata": {**(metadata or {}), "reason": reason},
            "updated_at": _utc_now(),
        }
        # Deliberately do not claim ownership of preserved bytes.
        self.state["units"][unit_id] = record
        self._write()
        return record

    def fail(
        self,
        unit_id: str,
        paths: Iterable[str | Path],
        *,
        error: str,
        unit_inputs: Any = None,
    ) -> None:
        safe_paths = tuple(_safe_relative(path) for path in paths)
        self.state["units"][unit_id] = {
            "fingerprint": self._unit_fingerprint(unit_id, unit_inputs),
            "status": "failed",
            "outputs": self._actual_outputs(safe_paths),
            "error": error[:4000],
            "updated_at": _utc_now(),
        }
        self._write()

    def finish(self) -> None:
        self.state["status"] = "completed"
        self._write()


__all__ = [
    "CHECKPOINT_RELATIVE_PATH",
    "CheckpointDecision",
    "GenerationCheckpoint",
    "canonical_sha256",
    "file_sha256",
]
