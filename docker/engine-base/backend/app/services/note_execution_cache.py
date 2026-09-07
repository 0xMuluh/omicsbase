"""Explicit, provenance-keyed reuse for completed NoteThread executions."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy.orm import Session, selectinload

from app.models.notes import (
    CellExecution,
    NoteCell,
    NoteCellRevision,
    NoteExecutionArtifact,
    NoteThread,
)

CACHE_KEY_VERSION = "note-cache-v1"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _digest(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        default=str,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def dependency_fingerprint(executions: Sequence[CellExecution]) -> str:
    """Digest explicit upstream execution outputs, independent of their IDs."""
    records = []
    for execution in executions:
        artifacts = sorted(
            (
                artifact.artifact_type,
                artifact.relative_path,
                artifact.sha256,
                artifact.byte_size,
            )
            for artifact in execution.artifacts
        )
        records.append(
            {
                "cache_key": execution.cache_key,
                "input_fingerprint": execution.input_fingerprint,
                "environment_fingerprint": execution.environment_fingerprint,
                "artifacts": artifacts,
            }
        )
    return _digest({"version": CACHE_KEY_VERSION, "upstream": records})


def execution_cache_key(
    *,
    input_fingerprint: str,
    environment_fingerprint: str,
    dependency_fingerprint: str,
    timeout_seconds: int,
) -> str:
    """Build the stable key for one explicit execution contract."""
    return _digest(
        {
            "version": CACHE_KEY_VERSION,
            "input_fingerprint": input_fingerprint,
            "environment_fingerprint": environment_fingerprint,
            "dependency_fingerprint": dependency_fingerprint,
            "timeout_seconds": timeout_seconds,
        }
    )


def resolve_upstream_executions(
    db: Session,
    project_id: str | None,
    execution_ids: Sequence[str],
    *,
    thread_id: str | None = None,
) -> list[CellExecution]:
    """Resolve completed upstream executions within the same workspace."""
    requested = [str(item) for item in execution_ids]
    if len(requested) > 64:
        raise ValueError("At most 64 upstream executions may be declared")
    if len(set(requested)) != len(requested):
        raise ValueError("upstream_execution_ids must not contain duplicates")
    if not requested:
        return []

    if thread_id is None and project_id is None:
        raise ValueError("A project or NoteThread scope is required")
    scope_filters = [CellExecution.id.in_(requested)]
    if thread_id is not None:
        scope_filters.append(NoteThread.id == thread_id)
    else:
        scope_filters.append(NoteThread.project_id == project_id)
    rows = (
        db.query(CellExecution)
        .join(NoteCellRevision, NoteCellRevision.id == CellExecution.revision_id)
        .join(NoteCell, NoteCell.id == NoteCellRevision.cell_id)
        .join(NoteThread, NoteThread.id == NoteCell.thread_id)
        .options(selectinload(CellExecution.artifacts))
        .filter(*scope_filters)
        .all()
    )
    found = {str(row.id): row for row in rows}
    missing = [item for item in requested if item not in found]
    if missing:
        raise LookupError("One or more upstream executions were not found in this workspace")
    incomplete = [item for item in rows if item.status != "completed"]
    if incomplete:
        raise RuntimeError("All upstream executions must be completed before reuse is enabled")
    return [found[item] for item in requested]


def _safe_artifact_path(project_dir: str, artifact: NoteExecutionArtifact) -> Path | None:
    base = Path(project_dir).resolve()
    relative_path = Path(artifact.relative_path)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        return None
    path = (base / relative_path).resolve()
    if base not in path.parents or not path.is_file():
        return None
    return path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifacts_are_valid(project_dir: str, artifacts: Sequence[NoteExecutionArtifact]) -> bool:
    if not artifacts:
        return False
    for artifact in artifacts:
        path = _safe_artifact_path(project_dir, artifact)
        if path is None:
            return False
        if path.stat().st_size != artifact.byte_size:
            return False
        if _sha256_file(path) != artifact.sha256:
            return False
    return True


def find_reusable_execution(
    db: Session,
    *,
    project_id: str | None,
    cache_key: str,
    project_dir: str,
    exclude_execution_id: str | None = None,
    thread_id: str | None = None,
) -> CellExecution | None:
    """Find the newest completed result whose immutable bytes still validate."""
    if thread_id is None and project_id is None:
        raise ValueError("A project or NoteThread scope is required")
    scope_filters = [
        CellExecution.status == "completed",
        CellExecution.cache_key == cache_key,
    ]
    if thread_id is not None:
        scope_filters.append(NoteThread.id == thread_id)
    else:
        scope_filters.append(NoteThread.project_id == project_id)
    query = (
        db.query(CellExecution)
        .join(NoteCellRevision, NoteCellRevision.id == CellExecution.revision_id)
        .join(NoteCell, NoteCell.id == NoteCellRevision.cell_id)
        .join(NoteThread, NoteThread.id == NoteCell.thread_id)
        .options(selectinload(CellExecution.artifacts))
        .filter(*scope_filters)
        .order_by(CellExecution.finished_at.desc(), CellExecution.created_at.desc())
    )
    if exclude_execution_id:
        query = query.filter(CellExecution.id != exclude_execution_id)
    for candidate in query.all():
        if _artifacts_are_valid(project_dir, candidate.artifacts):
            return candidate
    return None


def reuse_cached_execution(
    db: Session,
    *,
    execution: CellExecution,
    source: CellExecution,
) -> None:
    """Materialize a cache hit as a new execution with copied provenance rows."""
    cache_metadata = {
        "hit": True,
        "source_execution_id": str(source.id),
        "source_cache_key": source.cache_key,
    }
    result_metadata = copy.deepcopy(source.result_metadata or {})
    result_metadata["cache"] = cache_metadata
    provenance = result_metadata.get("provenance")
    if isinstance(provenance, dict):
        result_metadata["provenance"] = {
            **provenance,
            "execution_id": str(execution.id),
            "cache_source_execution_id": str(source.id),
        }

    copied_artifacts = []
    for source_artifact in source.artifacts:
        artifact_metadata = copy.deepcopy(source_artifact.artifact_metadata or {})
        artifact_metadata["cache"] = cache_metadata
        source_provenance = artifact_metadata.get("provenance")
        if isinstance(source_provenance, dict):
            artifact_metadata["provenance"] = {
                **source_provenance,
                "execution_id": str(execution.id),
                "cache_source_execution_id": str(source.id),
                "source_artifact_id": str(source_artifact.id),
            }
        copied = NoteExecutionArtifact(
            execution_id=execution.id,
            artifact_type=source_artifact.artifact_type,
            relative_path=source_artifact.relative_path,
            mime_type=source_artifact.mime_type,
            byte_size=source_artifact.byte_size,
            sha256=source_artifact.sha256,
            artifact_metadata=artifact_metadata,
        )
        db.add(copied)
        copied_artifacts.append(copied)

    db.flush()
    result_metadata["artifacts"] = [
        {
            "id": str(artifact.id),
            "artifact_type": artifact.artifact_type,
            "relative_path": artifact.relative_path,
            "mime_type": artifact.mime_type,
            "byte_size": artifact.byte_size,
            "sha256": artifact.sha256,
            "metadata": artifact.artifact_metadata,
        }
        for artifact in copied_artifacts
    ]
    execution.status = "completed"
    execution.execution_kind = "cached"
    execution.cache_hit = True
    execution.cache_source_execution_id = str(source.id)
    execution.started_at = _now()
    execution.finished_at = execution.started_at
    execution.error = None
    execution.result_metadata = result_metadata

