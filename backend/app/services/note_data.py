"""Thread-attached data files for NoteThreads (standalone and workspace)."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.file_inspector import inspect_file
from app.services.note_scope import thread_storage_path
from app.services.runner import run_command_sync

logger = logging.getLogger(__name__)

MAX_THREAD_UPLOAD_BYTES = 50 * 1024 * 1024
SAFE_NAME_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"


def thread_uploads_dir(thread: Any, project: Any | None = None) -> Path:
    """Return the private uploads root for one NoteThread.

    Standalone threads keep files under their storage root; workspace threads
    keep them inside the project dir so R cells (cwd = storage root) can read
    them via a stable relative path.
    """
    root = thread_storage_path(thread, project)
    if getattr(thread, "project_id", None):
        directory = root / ".omicsbase" / "note-uploads" / str(thread.id)
    else:
        directory = root / "uploads"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _r_path(thread: Any, filename: str) -> str:
    if getattr(thread, "project_id", None):
        return f".omicsbase/note-uploads/{thread.id}/{filename}"
    return f"uploads/{filename}"


def _safe_filename(filename: str) -> str:
    safe = Path(str(filename or "upload.bin")).name
    safe = "".join(ch for ch in safe if ch in SAFE_NAME_CHARS).strip("._")
    return safe or "upload.bin"


def list_thread_data_files(thread: Any, project: Any | None = None) -> list[dict[str, Any]]:
    """Summarize every data file attached to the thread, newest first.

    For workspace threads the project's study uploads are included too
    (they already live under the project dir), so the agent sees both
    thread-attached files and study files with correct r_path values.
    """
    seen: set[str] = set()
    summaries: list[dict[str, Any]] = []

    def _append(path: Path, r_path: str) -> None:
        if path.name in seen or path.name.startswith("."):
            return
        seen.add(path.name)
        summary = inspect_file(str(path))
        summaries.append(
            {
                "name": path.name,
                "format": summary.get("format", "unknown"),
                "size_bytes": path.stat().st_size,
                "dimensions": summary.get("dimensions"),
                "columns": (summary.get("columns") or [])[:60],
                "note": summary.get("note"),
                "r_path": r_path,
            }
        )

    directory = thread_uploads_dir(thread, project)
    for path in sorted(directory.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.is_file():
            _append(path, _r_path(thread, path.name))

    if getattr(thread, "project_id", None) and project is not None and getattr(project, "id", None):
        uploads_dir = Path(settings.projects_dir).resolve() / "uploads" / str(project.id)
        if uploads_dir.is_dir():
            for path in sorted(uploads_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                if path.is_file():
                    _append(path, f"../uploads/{project.id}/{path.name}")

    return summaries


def save_thread_upload(
    thread: Any,
    content: bytes,
    *,
    filename: str,
    project: Any | None = None,
) -> dict[str, Any]:
    """Persist one uploaded file into the thread and return its summary.

    Rejects unknown formats so unsupported uploads fail fast with a clear
    message instead of becoming files the agent cannot inspect.
    """
    if len(content) > MAX_THREAD_UPLOAD_BYTES:
        raise ValueError(f"File exceeds the {MAX_THREAD_UPLOAD_BYTES // (1024 * 1024)} MB upload limit")
    if not content:
        raise ValueError("The uploaded file is empty")

    name = _safe_filename(filename)
    destination = thread_uploads_dir(thread, project) / name
    destination.write_bytes(content)
    summary = inspect_file(str(destination))
    detected = summary.get("format")
    if detected == "error":
        destination.unlink(missing_ok=True)
        detail = summary.get("error", "unsupported format")
        raise ValueError(f"Failed to inspect file {name}: {detail}")
    if not detected or detected == "unknown":
        ext = Path(name).suffix.lstrip(".").lower()
        summary["format"] = ext or "file"
    return {
        **summary,
        "name": name,
        "size_bytes": destination.stat().st_size,
        "columns": (summary.get("columns") or [])[:60],
        "r_path": _r_path(thread, name),
    }


def import_dataset_into_thread(
    thread: Any,
    *,
    package: str,
    dataset: str,
    project: Any | None = None,
) -> dict[str, Any]:
    """Export a known R package dataset into the thread's uploads root."""
    from app.services.data_acquisition import PACKAGE_DATASETS, _package_export_script

    package = str(package or "").strip()
    dataset = str(dataset or "").strip()
    key = (package, dataset)
    if key not in PACKAGE_DATASETS:
        known = ", ".join(f"{p}::{d}" for p, d in PACKAGE_DATASETS)
        raise ValueError(f"Dataset {package}::{dataset} is not importable. Known: {known}")

    upload_dir = thread_uploads_dir(thread, project)
    prefix = "".join(ch if ch in SAFE_NAME_CHARS else "_" for ch in f"{package}_{dataset}")

    with tempfile.TemporaryDirectory(prefix="omicsbase-note-import-") as tmp:
        tmp_path = Path(tmp)
        script = tmp_path / "export.R"
        script.write_text(_package_export_script(package, dataset, tmp_path), encoding="utf-8")
        try:
            success, run_output = run_command_sync(
                ["Rscript", script.name],
                cwd=str(tmp_path),
                timeout=120,
            )
        except FileNotFoundError as exc:
            raise ValueError("Rscript is not available in this environment") from exc
        if not success:
            raise ValueError(f"Failed to import dataset: {run_output[:500]}")

        exported = sorted(tmp_path.glob(f"{prefix}*.csv")) + sorted(tmp_path.glob(f"{prefix}*.tsv"))
        if not exported:
            raise ValueError("R export produced no CSV/TSV files")

        # Copy while the temp dir still exists (it is removed on exit).
        registered: list[dict[str, Any]] = []
        for path in exported:
            destination = upload_dir / path.name
            destination.write_bytes(path.read_bytes())
            registered.append(
                {
                    "name": destination.name,
                    "format": "csv",
                    "size_bytes": destination.stat().st_size,
                    "r_path": _r_path(thread, destination.name),
                }
            )
    return {"status": "ok", "package": package, "dataset": dataset, "files": registered}
