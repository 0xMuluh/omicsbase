"""Report-pack catalog and copy helper.

A ReportPack is a worked example under templates/ (omicsbase-pack.yaml):
how this lab writes a Quarto analysis website. This module lists those
trees so a coding runtime can read them, and copies one only when
something **explicitly** asks. It does not guess a pack from data and
does not treat packs as parameter forms.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.report_pack import (
    MANIFEST_NAME,
    ReportPack,
    ReportPackError,
    load_report_pack,
    report_pack_source_files,
)

# App root derived from the correctly-resolved prompts_dir (works in both the
# host repo layout and the container layout, which differ in path depth).
_TEMPLATE_ROOT = Path(settings.prompts_dir).resolve().parent / "templates"


def _catalog_roots() -> list[Path]:
    roots = [_TEMPLATE_ROOT]
    if settings.report_packs_dir.strip():
        roots.append(Path(settings.report_packs_dir).expanduser().resolve())
    unique: list[Path] = []
    for root in roots:
        resolved = root.resolve()
        if resolved not in unique and resolved.is_dir():
            unique.append(resolved)
    return unique


def report_pack_catalog() -> dict[str, ReportPack]:
    """Discover declared packs under administrator-controlled catalog roots."""
    catalog: dict[str, ReportPack] = {}
    manifest_paths: set[Path] = set()
    for catalog_root in _catalog_roots():
        for manifest in sorted(catalog_root.rglob(MANIFEST_NAME)):
            resolved_manifest = manifest.resolve()
            if resolved_manifest in manifest_paths:
                continue
            manifest_paths.add(resolved_manifest)
            pack = load_report_pack(manifest.parent)
            existing = catalog.get(pack.pack_id)
            if existing is not None and existing.root != pack.root:
                raise ReportPackError(
                    f"Duplicate ReportPack id {pack.pack_id!r}: "
                    f"{existing.root} and {pack.root}"
                )
            catalog[pack.pack_id] = pack
    return catalog


def list_report_packs() -> list[dict[str, Any]]:
    """Return client-safe catalog metadata without exposing server paths."""
    items: list[dict[str, Any]] = []
    for pack in report_pack_catalog().values():
        execution = pack.execution.as_dict() if pack.execution else None
        items.append(
            {
                "id": pack.pack_id,
                "version": pack.version,
                "domain": pack.domain,
                "name": pack.name,
                "entrypoint": pack.entrypoint,
                "execution": execution,
                "capabilities": [item.as_dict() for item in pack.capabilities],
                "source_tree_sha256": pack.source_tree_sha256,
            }
        )
    return sorted(items, key=lambda item: (item["domain"], item["name"], item["id"]))


def resolve_report_pack(
    pack_id: str | None,
    *,
    domain: str,
) -> ReportPack | None:
    """Resolve an explicitly selected pack. None/empty means no pack (from-scratch)."""
    catalog = report_pack_catalog()
    wanted = (pack_id or "").strip()
    if not wanted:
        return None
    pack = catalog.get(wanted)
    if pack is None:
        raise ReportPackError(f"Unknown ReportPack id: {wanted}")
    if pack.domain != domain:
        raise ReportPackError(
            f"ReportPack {wanted!r} is for domain {pack.domain!r}, not {domain!r}"
        )
    return pack


def format_report_pack_catalog_for_llm() -> str:
    """List worked-example trees so a coding runtime can read how this lab reports."""
    catalog = report_pack_catalog()
    if not catalog:
        return "No example analyses are installed under the templates catalog."
    lines = [
        "Finished analyses from this lab — reference for how a project is laid out, how the data container and helpers are written, and how the site is rendered:",
    ]
    for pack in sorted(catalog.values(), key=lambda item: (item.domain, item.pack_id)):
        lines.append(
            f"- {pack.pack_id} ({pack.domain}): {pack.name} — {pack.root}"
        )
    return "\n".join(lines)


def spawn_report_pack(
    project_dir: str,
    pack: ReportPack,
    *,
    overwrite_edits: bool = True,
) -> dict[str, str]:
    """Copy one resolved ReportPack's source tree into the workspace.

    The template is authoritative: existing files are overwritten unless
    ``overwrite_edits`` is disabled, which preserves already-adapted files.
    """
    root = pack.root
    base = Path(project_dir)
    spawned: dict[str, str] = {}

    for exemplar in report_pack_source_files(root):
        relative_path = exemplar.relative_to(root).as_posix()
        target = base / relative_path
        if target.exists() and not overwrite_edits:
            try:
                spawned[relative_path] = target.read_text(errors="replace")
            except OSError:
                spawned[relative_path] = ""
            continue
        # The template is authoritative: overwrite thin scaffold placeholders.
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(exemplar, target)
        try:
            spawned[relative_path] = target.read_text(errors="replace")
        except OSError:
            spawned[relative_path] = ""
    return spawned
