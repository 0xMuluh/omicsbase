"""Discovery and safe serving metadata for rendered Quarto reports.

The workspace agent is intentionally free to choose a Quarto layout.  This
module is the small integration boundary between that layout and the browser:
it records the report root and entry page without requiring ``output/``.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import yaml


REPORT_MANIFEST_RELATIVE = Path(".omicsbase") / "report_manifest.json"
REPORT_MANIFEST_VERSION = "1.0"
REPORT_ENTRY_NAMES = ("index.html", "report.html", "summary.html", "overview.html")
_SKIP_PARTS = {
    ".git",
    ".omicsbase",
    "data",
    "node_modules",
    "site_libs",
    "_freeze",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalise_relative(value: str | Path | None) -> str | None:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return None
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        return None
    normalised = path.as_posix().lstrip("./")
    if not normalised or normalised == ".":
        return None
    return normalised


def _relative_path(root: Path, path: Path) -> str | None:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return None


def _within(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _source_candidates(project_root: Path) -> list[Path]:
    """Find project-local Quarto source roots without inspecting private dirs."""
    candidates: list[Path] = []
    for config in sorted(project_root.rglob("_quarto.yml")):
        relative = config.relative_to(project_root)
        if any(part in _SKIP_PARTS for part in relative.parts[:-1]):
            continue
        candidates.append(config.parent.resolve())
    return list(dict.fromkeys(candidates))


def infer_source_dir(project_dir: str | Path) -> Path | None:
    """Infer the unique source directory used by an agent-built project."""
    root = Path(project_dir).resolve()
    candidates = _source_candidates(root)
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        return None

    qmd_files = [
        path.resolve()
        for path in sorted(root.rglob("*.qmd"))
        if not any(part in _SKIP_PARTS for part in path.relative_to(root).parts)
    ]
    if not qmd_files:
        return None
    try:
        common = Path(os.path.commonpath([str(path.parent) for path in qmd_files]))
    except ValueError:
        return None
    return common if _within(root, common) else None


def configured_report_root(project_dir: str | Path, source_dir: str | Path | None = None) -> Path | None:
    """Resolve Quarto's configured/default output root inside the project.

    ``output-dir`` is read from the project's own Quarto configuration.  When
    it is absent, Quarto's website default (``_site``) is used.  Projects with
    no configuration retain the legacy ``output/`` compatibility location.
    """
    root = Path(project_dir).resolve()
    source = Path(source_dir).resolve() if source_dir else infer_source_dir(root)
    if source is None or not _within(root, source):
        return root / "output"

    config_path = source / "_quarto.yml"
    if not config_path.is_file():
        return root / "output"
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        config = {}
    project_config = config.get("project") if isinstance(config, dict) else None
    project_config = project_config if isinstance(project_config, dict) else {}
    configured = project_config.get("output-dir")
    if isinstance(configured, str) and configured.strip():
        candidate = (source / configured.strip()).resolve()
        return candidate if _within(root, candidate) else None

    if str(project_config.get("type") or "").strip().lower() == "website":
        return (source / "_site").resolve()
    # A document project emits beside its source when no output-dir is set.
    return source


def _html_files(report_root: Path) -> list[Path]:
    if not report_root.is_dir():
        return []
    files: list[Path] = []
    for path in sorted(report_root.rglob("*.html")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            relative = path.relative_to(report_root)
        except ValueError:
            continue
        if any(part in _SKIP_PARTS for part in relative.parts[:-1]):
            continue
        files.append(path)
    return files


def _is_generated_wrapper(path: Path) -> bool:
    """Identify the compatibility iframe wrapper emitted by the legacy runner."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:100_000].lower()
    except OSError:
        return False
    return "omicsbase report" in text and "<iframe name=\"preview\"" in text


def _candidate_roots(
    project_root: Path,
    *,
    source_dir: Path | None = None,
    preferred_root: Path | None = None,
) -> list[Path]:
    roots: list[Path] = []
    for candidate in (
        preferred_root,
        configured_report_root(project_root, source_dir),
        project_root / "output",
        project_root / "_site",
        source_dir,
    ):
        if candidate is None:
            continue
        resolved = candidate.resolve()
        if _within(project_root, resolved) and resolved not in roots:
            roots.append(resolved)
    return roots


def _entrypoint(files: list[Path], report_root: Path, expected_pages: Iterable[str] | None) -> Path | None:
    by_relative = {
        path.relative_to(report_root).as_posix(): path
        for path in files
    }
    expected = [str(page).replace("\\", "/") for page in (expected_pages or ())]
    for page in expected:
        candidate = Path(page).with_suffix(".html").as_posix()
        if candidate in by_relative:
            if Path(page).name.lower() in {"index.qmd", "report.qmd", "summary.qmd", "overview.qmd"}:
                return by_relative[candidate]
    for name in REPORT_ENTRY_NAMES:
        if name in by_relative:
            return by_relative[name]
    return files[0] if files else None


def discover_report_manifest(
    project_dir: str | Path,
    *,
    source_dir: str | Path | None = None,
    expected_pages: Iterable[str] | None = None,
    preferred_root: str | Path | None = None,
) -> dict[str, Any] | None:
    """Discover a usable HTML report and return its project-relative manifest."""
    root = Path(project_dir).resolve()
    source = Path(source_dir).resolve() if source_dir else infer_source_dir(root)
    preferred = Path(preferred_root).resolve() if preferred_root else None
    expected = list(expected_pages or ())
    best: tuple[tuple[int, int, int], Path, list[Path]] | None = None

    for candidate in _candidate_roots(root, source_dir=source, preferred_root=preferred):
        files = _html_files(candidate)
        if not files:
            continue
        relative_files = {path.relative_to(candidate).as_posix() for path in files}
        expected_hits = sum(
            Path(page).with_suffix(".html").as_posix() in relative_files
            for page in expected
        )
        entry = _entrypoint(files, candidate, expected)
        if entry is None:
            continue
        # Prefer roots matching rendered QMD pages, then roots with an entry
        # page, and finally the stable candidate order.
        score = (expected_hits, 1 if entry.name.lower() in REPORT_ENTRY_NAMES else 0, 0 if _is_generated_wrapper(entry) else 1, len(files))
        if best is None or score > best[0]:
            best = (score, candidate, files)

    if best is None:
        return None
    _score, report_root, files = best
    entrypoint = _entrypoint(files, report_root, expected)
    if entrypoint is None:
        return None
    report_root_relative = _relative_path(root, report_root)
    entry_relative = _relative_path(report_root, entrypoint)
    if not report_root_relative or not entry_relative:
        return None
    source_relative = _relative_path(root, source) if source else None
    return {
        "schema_version": REPORT_MANIFEST_VERSION,
        "status": "ready",
        "report_root": report_root_relative,
        "entrypoint": entry_relative,
        "preview_path": f"{report_root_relative}/{entry_relative}",
        "pages": sorted(path.relative_to(report_root).as_posix() for path in files),
        "source_dir": source_relative,
        "generated_at": _now(),
    }


def _validate_manifest(root: Path, value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("status") != "ready":
        return None
    report_root = _normalise_relative(value.get("report_root"))
    entrypoint = _normalise_relative(value.get("entrypoint"))
    if not report_root or not entrypoint or not entrypoint.lower().endswith(".html"):
        return None
    report_path = (root / report_root / entrypoint).resolve()
    report_root_path = (root / report_root).resolve()
    if not _within(root, report_root_path) or not _within(report_root_path, report_path):
        return None
    preview_path = f"{report_root}/{entrypoint}"
    pages = [
        normalised
        for item in (value.get("pages") or [])
        if (normalised := _normalise_relative(item)) and normalised.lower().endswith(".html")
    ]
    return {
        **value,
        "schema_version": str(value.get("schema_version") or REPORT_MANIFEST_VERSION),
        "status": "ready",
        "report_root": report_root,
        "entrypoint": entrypoint,
        "preview_path": preview_path,
        "pages": sorted(set(pages)),
    }


def load_report_manifest(project_dir: str | Path) -> dict[str, Any] | None:
    root = Path(project_dir).resolve()
    path = root / REPORT_MANIFEST_RELATIVE
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    manifest = _validate_manifest(root, value)
    if not manifest:
        return None
    report_path = root / manifest["preview_path"]
    return manifest if report_path.is_file() and not report_path.is_symlink() else None


def report_manifest_for_project(project_dir: str | Path) -> dict[str, Any] | None:
    """Return persisted metadata, or discover a legacy report read-only."""
    persisted = load_report_manifest(project_dir)
    if persisted:
        return persisted
    return discover_report_manifest(project_dir)


def write_report_manifest(project_dir: str | Path, manifest: dict[str, Any]) -> dict[str, Any]:
    root = Path(project_dir).resolve()
    validated = _validate_manifest(root, manifest)
    if not validated:
        raise ValueError("Cannot persist an invalid report manifest")
    path = root / REPORT_MANIFEST_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(validated, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return validated


def discover_and_write_report_manifest(
    project_dir: str | Path,
    *,
    source_dir: str | Path | None = None,
    expected_pages: Iterable[str] | None = None,
    preferred_root: str | Path | None = None,
) -> dict[str, Any] | None:
    manifest = discover_report_manifest(
        project_dir,
        source_dir=source_dir,
        expected_pages=expected_pages,
        preferred_root=preferred_root,
    )
    if manifest is None:
        return None
    return write_report_manifest(project_dir, manifest)


def resolve_report_file(project_dir: str | Path, requested_path: str, manifest: dict[str, Any] | None) -> Path:
    """Resolve a browser report URL beneath the discovered report root."""
    root = Path(project_dir).resolve()
    requested = _normalise_relative(requested_path)
    if not requested:
        raise ValueError("Invalid report path")
    if manifest:
        report_root = str(manifest["report_root"])
        entrypoint = str(manifest["entrypoint"])
        if requested == str(manifest.get("preview_path")) or requested == "index.html":
            relative = entrypoint
        elif requested == report_root:
            relative = entrypoint
        elif requested.startswith(report_root + "/"):
            relative = requested[len(report_root) + 1 :]
        else:
            relative = requested
        base = (root / report_root).resolve()
        candidate = (base / relative).resolve()
        if not _within(base, candidate):
            raise ValueError("Report path escapes report root")
        return candidate

    # Legacy projects without a manifest remain readable from output/, but no
    # path may escape that directory.
    base = (root / "output").resolve()
    candidate = (base / requested).resolve()
    if not _within(base, candidate):
        raise ValueError("Report path escapes output root")
    return candidate


__all__ = [
    "REPORT_MANIFEST_RELATIVE",
    "configured_report_root",
    "discover_and_write_report_manifest",
    "discover_report_manifest",
    "infer_source_dir",
    "load_report_manifest",
    "report_manifest_for_project",
    "resolve_report_file",
    "write_report_manifest",
]
