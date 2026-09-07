"""Report-pack catalog.

A ReportPack is a worked example under templates/ (omicsbase-pack.yaml):
how this lab writes a Quarto analysis website. This module discovers those
trees so a coding runtime can read them. It does not guess a pack from data
and does not treat packs as parameter forms.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import settings
from app.services.report_pack import (
    MANIFEST_NAME,
    ReportPack,
    ReportPackError,
    load_report_pack,
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

