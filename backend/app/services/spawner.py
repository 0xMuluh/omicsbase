"""Spawn generated projects as verbatim copies of the exemplar template projects.

The template IS the report: every new project is the domain exemplar's full
project tree (data construction scripts, helper functions, orchestration,
Quarto pages, site configuration) copied verbatim, then adapted to the study
by the agent. Nothing is invented on top of the template.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from app.config import settings
from app.schemas.schemas import AnalysisPlan
from app.services.generation_checkpoint import GenerationCheckpoint, file_sha256
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

# Canonical exemplar project per domain.
EXEMPLAR_ROOTS: dict[str, Path] = {
    "microbiome": _TEMPLATE_ROOT / "microbiome" / "microbiota_diversity_pipeline",
    "metabolomics": _TEMPLATE_ROOT / "metabolomics" / "prenatal_diet_metabolomics",
}
DEFAULT_PACK_IDS = {
    "microbiome": "microbiome-diversity",
    "metabolomics": "prenatal-metabolomics",
}


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
    """Resolve an explicit/catalog default pack without accepting a raw path."""
    catalog = report_pack_catalog()
    wanted = (pack_id or DEFAULT_PACK_IDS.get(domain) or "").strip()
    if not wanted:
        domain_matches = [pack for pack in catalog.values() if pack.domain == domain]
        return domain_matches[0] if len(domain_matches) == 1 else None
    pack = catalog.get(wanted)
    if pack is None:
        raise ReportPackError(f"Unknown ReportPack id: {wanted}")
    if pack.domain != domain:
        raise ReportPackError(
            f"ReportPack {wanted!r} is for domain {pack.domain!r}, not {domain!r}"
        )
    return pack


def format_report_pack_catalog_for_llm() -> str:
    items = list_report_packs()
    if not items:
        return "(No declared ReportPacks are available.)"
    return "\n".join(
        f"- {item['id']}: {item['name']} (domain={item['domain']}, version={item['version']}; "
        f"capabilities={', '.join(capability['id'] for capability in item.get('capabilities', [])) or 'none'})"
        for item in items
    )

def exemplar_root(domain: str) -> Path | None:
    """Return the exemplar project root for a domain, or None."""
    root = EXEMPLAR_ROOTS.get(domain)
    return root if root is not None and root.exists() else None


def exemplar_report_pack(domain: str) -> ReportPack | None:
    """Load the declared pack, or conservative discovery, for a domain exemplar."""
    return resolve_report_pack(None, domain=domain)


def exemplar_project_files(domain: str) -> list[Path]:
    """Return every safe report-source file in the complete exemplar pack."""
    root = exemplar_root(domain)
    if root is None:
        return []
    return report_pack_source_files(root)


def spawn_exemplar_project(project_dir: str, plan: AnalysisPlan) -> dict[str, str]:
    """Copy the exemplar project tree verbatim into the new project.

    Returns {relative_path: content} for files written or already present.
    Files that already exist (from the scaffold) are kept and returned so the
    adapt stage still covers them.
    """
    pack = resolve_report_pack(plan.report_pack_id, domain=plan.domain)
    if pack is None:
        return {}
    return spawn_report_pack(project_dir, pack)


def spawn_report_pack(
    project_dir: str,
    pack: ReportPack,
    *,
    checkpoint: GenerationCheckpoint | None = None,
) -> dict[str, str]:
    """Copy one resolved ReportPack's source tree without clobbering edits.

    With a checkpoint, a matching completed copy is reused and files whose
    bytes diverged from the last generator-owned hash are preserved.  The
    checkpoint-free behavior remains a verbatim copy for compatibility with
    callers that explicitly request a fresh spawn.
    """
    root = pack.root
    base = Path(project_dir)
    spawned: dict[str, str] = {}

    for exemplar in report_pack_source_files(root):
        relative_path = exemplar.relative_to(root).as_posix()
        target = base / relative_path
        unit_id = f"spawn:{relative_path}"
        unit_inputs = {"template_sha256": file_sha256(exemplar)}
        if checkpoint is not None:
            decision = checkpoint.decide(
                unit_id,
                [relative_path],
                unit_inputs=unit_inputs,
            )
            if decision.action == "preserve":
                checkpoint.preserve(
                    unit_id,
                    [relative_path],
                    reason=decision.reason,
                    unit_inputs=unit_inputs,
                )
                try:
                    spawned[relative_path] = target.read_text(errors="replace")
                except OSError:
                    spawned[relative_path] = ""
                continue
            if decision.action == "skip":
                try:
                    spawned[relative_path] = target.read_text(errors="replace")
                except OSError:
                    spawned[relative_path] = ""
                continue
        # The template is authoritative: overwrite thin scaffold placeholders.
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(exemplar, target)
        if checkpoint is not None:
            checkpoint.complete(
                unit_id,
                [relative_path],
                unit_inputs=unit_inputs,
            )
        try:
            spawned[relative_path] = target.read_text(errors="replace")
        except OSError:
            spawned[relative_path] = ""
    return spawned
