"""Runtime resolution of ReportPack capabilities for one analysis plan."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from app.services.edit_validation import ValidationResult, validate_project_paths
from app.services.report_pack import ReportPack, ReportPackCapability


CAPABILITY_CONTRACT_RELATIVE = Path(".omicsbase") / "capabilities.json"
CAPABILITY_CONTRACT_VERSION = "1.0"


class CapabilityContractError(ValueError):
    """Raised when a plan asks for an unavailable capability."""


_CONTRACT_PACK_FIELDS = {"id", "version", "manifest_sha256"}
_CONTRACT_CAPABILITY_FIELDS = {
    "id",
    "sources",
    "execution_steps",
    "parameters",
    "outputs",
    "validators",
    "requested_by_plan",
}


def _safe_relative(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise CapabilityContractError(f"{label} must be a string")
    raw = value.strip().replace("\\", "/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise CapabilityContractError(f"{label} must be a safe relative path")
    return path.as_posix()


def _safe_path_list(value: Any, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise CapabilityContractError(f"{label} must be a list")
    paths = tuple(_safe_relative(item, label=label) for item in value)
    if len(paths) != len(set(paths)):
        raise CapabilityContractError(f"{label} contains duplicate paths")
    return paths


@dataclass(frozen=True)
class ResolvedCapability:
    capability: ReportPackCapability
    requested_by_plan: bool

    def as_dict(self) -> dict[str, Any]:
        return {**self.capability.as_dict(), "requested_by_plan": self.requested_by_plan}


@dataclass(frozen=True)
class CapabilityContract:
    pack_id: str
    pack_version: str
    manifest_sha256: str
    selected: tuple[ResolvedCapability, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CAPABILITY_CONTRACT_VERSION,
            "report_pack": {
                "id": self.pack_id,
                "version": self.pack_version,
                "manifest_sha256": self.manifest_sha256,
            },
            "capabilities": [item.as_dict() for item in self.selected],
        }


def _plan_mapping(plan: Any) -> dict[str, Any]:
    if isinstance(plan, dict):
        return plan
    model_dump = getattr(plan, "model_dump", None)
    if callable(model_dump):
        value = model_dump()
        return value if isinstance(value, dict) else {}
    return {}


def resolve_plan_capabilities(pack: ReportPack, plan: Any = None) -> CapabilityContract:
    """Resolve explicit plan capability ids, defaulting to the full pack.

    A plan may use either ``capabilities: [id, ...]`` or the singular
    ``capability_id`` while older plans remain valid and receive all declared
    pack capabilities.
    """
    declared = {item.capability_id: item for item in pack.capabilities}
    mapping = _plan_mapping(plan)
    requested_raw = mapping.get("capabilities")
    if requested_raw is None and mapping.get("capability_id") is not None:
        requested_raw = [mapping.get("capability_id")]
    if requested_raw is None:
        requested = list(declared)
        explicit = False
    elif isinstance(requested_raw, list):
        requested = [str(item).strip().lower() for item in requested_raw if str(item).strip()]
        explicit = True
    else:
        raise CapabilityContractError("Analysis plan capabilities must be a list of ids")
    if len(requested) != len(set(requested)):
        raise CapabilityContractError("Analysis plan capabilities must not contain duplicates")
    unknown = sorted(set(requested) - set(declared))
    if unknown:
        raise CapabilityContractError(
            f"Analysis plan requested unavailable capability(ies): {', '.join(unknown)}"
        )
    selected = tuple(
        ResolvedCapability(declared[item], requested_by_plan=explicit)
        for item in requested
    )
    return CapabilityContract(
        pack_id=pack.pack_id,
        pack_version=pack.version,
        manifest_sha256=pack.manifest_sha256,
        selected=selected,
    )




def _parameter_values(plan: Any) -> dict[str, Any]:
    mapping = _plan_mapping(plan)
    values: dict[str, Any] = {}
    explicit = mapping.get("parameters")
    if isinstance(explicit, dict):
        values.update(explicit)
    for key, value in mapping.items():
        if key not in {"parameters", "workflow", "capabilities", "capability_id"} and value not in (None, "", [], {}):
            values.setdefault(str(key), value)
    workflow = mapping.get("workflow")
    if isinstance(workflow, list):
        for step in workflow:
            if isinstance(step, dict) and isinstance(step.get("parameters"), dict):
                for key, value in step["parameters"].items():
                    if value not in (None, "", [], {}):
                        values.setdefault(str(key), value)
    return values


def validate_plan_parameter_bindings(
    pack: ReportPack,
    plan: Any = None,
    *,
    capability_ids: Iterable[str] | None = None,
    strict: bool = False,
) -> dict[str, list[str]]:
    """Validate ReportPack ``required`` parameter declarations against a plan.

    ``strict=False`` preserves older plans that predate capability parameters;
    an explicitly supplied ``parameters`` object opts into hard validation.
    """
    mapping = _plan_mapping(plan)
    explicit_parameters = isinstance(mapping.get("parameters"), dict) and mapping.get("parameters") is not None
    selected_ids = {str(value).strip().lower() for value in (capability_ids or mapping.get("capabilities") or []) if str(value).strip()}
    selected = [item for item in pack.capabilities if not selected_ids or item.capability_id in selected_ids]
    values = _parameter_values(plan)
    missing: dict[str, list[str]] = {}
    for capability in selected:
        required = [
            str(key)
            for key, requirement in capability.parameters.items()
            if str(requirement).strip().lower() in {"required", "mandatory"}
            and key not in values
        ]
        if required:
            missing[capability.capability_id] = sorted(required)
    if missing and (strict or explicit_parameters):
        detail = "; ".join(f"{capability}: {', '.join(keys)}" for capability, keys in sorted(missing.items()))
        raise CapabilityContractError("Missing required capability parameter binding(s): " + detail)
    return missing


def write_capability_contract(
    project_root: str | Path,
    pack: ReportPack,
    plan: Any = None,
) -> Path:
    """Write and immediately validate the resolved runtime capability map."""
    root = Path(project_root).resolve()
    target = root / CAPABILITY_CONTRACT_RELATIVE
    target.parent.mkdir(parents=True, exist_ok=True)
    contract = resolve_plan_capabilities(pack, plan)
    target.write_text(json.dumps(contract.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    load_capability_contract(root)
    return target


def load_capability_contract(project_root: str | Path) -> CapabilityContract:
    root = Path(project_root).resolve()
    target = root / CAPABILITY_CONTRACT_RELATIVE
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CapabilityContractError(f"Invalid capability contract: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != CAPABILITY_CONTRACT_VERSION:
        raise CapabilityContractError("Unsupported capability contract schema")
    pack = raw.get("report_pack")
    values = raw.get("capabilities")
    if not isinstance(pack, dict) or not isinstance(values, list):
        raise CapabilityContractError("Capability contract is missing report_pack or capabilities")
    unknown_pack = sorted(set(pack) - _CONTRACT_PACK_FIELDS)
    if unknown_pack:
        raise CapabilityContractError(
            f"Capability contract report_pack contains unknown field(s): {', '.join(unknown_pack)}"
        )
    if not all(isinstance(pack.get(field), str) and str(pack.get(field)).strip() for field in _CONTRACT_PACK_FIELDS):
        raise CapabilityContractError("Capability contract report_pack metadata is incomplete")
    selected: list[ResolvedCapability] = []
    seen_ids: set[str] = set()
    for item in values:
        if not isinstance(item, dict) or not str(item.get("id") or "").strip():
            raise CapabilityContractError("Capability contract contains an invalid capability")
        unknown = sorted(set(item) - _CONTRACT_CAPABILITY_FIELDS)
        if unknown:
            raise CapabilityContractError(
                f"Capability contract contains unknown field(s): {', '.join(unknown)}"
            )
        capability_id = str(item["id"]).strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", capability_id):
            raise CapabilityContractError(f"Invalid capability id in contract: {capability_id!r}")
        if capability_id in seen_ids:
            raise CapabilityContractError(f"Duplicate capability in contract: {capability_id}")
        seen_ids.add(capability_id)
        sources = _safe_path_list(item.get("sources") or [], label=f"{capability_id}.sources")
        outputs = _safe_path_list(item.get("outputs") or [], label=f"{capability_id}.outputs")
        validators = _safe_path_list(item.get("validators") or [], label=f"{capability_id}.validators")
        execution_steps = item.get("execution_steps") or []
        if not isinstance(execution_steps, list) or any(not isinstance(value, str) or not value.strip() for value in execution_steps):
            raise CapabilityContractError(f"{capability_id}.execution_steps must be a list of ids")
        if not isinstance(item.get("parameters") or {}, dict):
            raise CapabilityContractError(f"{capability_id}.parameters must be a mapping")
        selected.append(
            ResolvedCapability(
                ReportPackCapability(
                    capability_id=capability_id,
                    sources=sources,
                    execution_steps=tuple(str(value).strip() for value in execution_steps),
                    parameters=dict(item.get("parameters") or {}),
                    outputs=outputs,
                    validators=validators,
                ),
                bool(item.get("requested_by_plan")),
            )
        )
    return CapabilityContract(
        pack_id=str(pack.get("id") or ""),
        pack_version=str(pack.get("version") or ""),
        manifest_sha256=str(pack.get("manifest_sha256") or ""),
        selected=tuple(selected),
    )


def validate_capability_bindings(
    project_root: str | Path,
    contract: CapabilityContract,
    *,
    run_r_parse: bool = False,
) -> ValidationResult:
    """Validate every declared capability validator against generated bytes.

    A capability is not considered executable merely because its manifest names
    a validator. The generated project must contain that validator and the
    source must pass the same structural checks used for transactional edits.
    Runtime execution of validator steps remains the runner's responsibility.
    """

    paths = sorted({
        path
        for item in contract.selected
        for path in item.capability.validators
    })
    if not paths:
        return ValidationResult(checks=["capability_validators:none"])
    result = validate_project_paths(project_root, paths, run_r_parse=run_r_parse)
    result.checks.append("capability_validators")
    return result


__all__ = [
    "CAPABILITY_CONTRACT_RELATIVE",
    "CapabilityContract",
    "CapabilityContractError",
    "load_capability_contract",
    "validate_capability_bindings",
    "validate_plan_parameter_bindings",
    "resolve_plan_capabilities",
    "write_capability_contract",
]
