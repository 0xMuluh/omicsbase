"""Validated runtime contract emitted from an adapted ReportPack.

The authored pack manifest describes intent. This generated contract freezes the
resolved execution order and render strategy for the project runner without
freezing study inputs or model-generated adaptations.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import yaml

from app.services.report_pack import (
    EXECUTABLE_FILE_ROLES,
    EXECUTION_RENDER_MODES,
    ReportPack,
)


CONTRACT_NAME = "execution_contract.json"
PACK_SNAPSHOT_NAME = "report_pack.yaml"
CONTRACT_SCHEMA_VERSION = "1.0"
MAX_CONTRACT_BYTES = 1_000_000
_CONTRACT_FIELDS = {
    "schema_version",
    "report_pack",
    "working_directory",
    "render",
    "entrypoint",
    "steps",
    "artifacts",
}
_PACK_FIELDS = {"id", "version", "domain", "manifest_sha256", "source_tree_sha256"}
_STEP_FIELDS = {"id", "path", "role"}


class ExecutionContractError(ValueError):
    """Raised when a generated execution contract is unsafe or malformed."""


def _strict_fields(value: dict[str, Any], allowed: set[str], *, label: str) -> None:
    unknown = sorted(set(value) - allowed)
    missing = sorted(allowed - set(value))
    if unknown:
        raise ExecutionContractError(
            f"Unknown {label} field(s): {', '.join(unknown)}"
        )
    if missing:
        raise ExecutionContractError(
            f"Missing {label} field(s): {', '.join(missing)}"
        )


def _normalise_relative(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise ExecutionContractError(f"{label} must be a string")
    raw = value.strip().replace("\\", "/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise ExecutionContractError(f"{label} must be a safe relative path")
    return path.as_posix()


def _resolve_project_path(
    project_root: Path,
    relative: str,
    *,
    label: str,
    require_file: bool = False,
    require_directory: bool = False,
) -> Path:
    candidate = project_root / relative
    resolved = candidate.resolve()
    try:
        resolved.relative_to(project_root)
    except ValueError as exc:
        raise ExecutionContractError(f"{label} escaped the project root") from exc
    if candidate.is_symlink():
        raise ExecutionContractError(f"{label} may not be a symlink: {relative}")
    if require_file and not resolved.is_file():
        raise ExecutionContractError(f"{label} does not exist: {relative}")
    if require_directory and not resolved.is_dir():
        raise ExecutionContractError(f"{label} does not exist: {relative}")
    return resolved


@dataclass(frozen=True)
class ExecutionStep:
    step_id: str
    path: str
    role: str

    def as_dict(self) -> dict[str, str]:
        return {"id": self.step_id, "path": self.path, "role": self.role}


@dataclass(frozen=True)
class ExecutionContract:
    project_root: Path
    report_pack: dict[str, str]
    working_directory: str
    render: str
    entrypoint: str | None
    steps: tuple[ExecutionStep, ...]
    artifacts: tuple[str, ...]

    @property
    def working_path(self) -> Path:
        return (self.project_root / self.working_directory).resolve()

    @property
    def entrypoint_path(self) -> Path | None:
        if self.entrypoint is None:
            return None
        return (self.project_root / self.entrypoint).resolve()

    def step_path(self, step: ExecutionStep) -> Path:
        return (self.project_root / step.path).resolve()

    def artifact_path(self, relative: str) -> Path:
        return (self.project_root / relative).resolve()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CONTRACT_SCHEMA_VERSION,
            "report_pack": dict(self.report_pack),
            "working_directory": self.working_directory,
            "render": self.render,
            "entrypoint": self.entrypoint,
            "steps": [step.as_dict() for step in self.steps],
            "artifacts": list(self.artifacts),
        }


def execution_contract_for_pack(
    project_root: str | Path,
    report_pack: ReportPack,
    *,
    excluded_paths: Iterable[str] = (),
) -> ExecutionContract | None:
    """Resolve the adapted ReportPack execution declaration in a generated tree.

    ``excluded_paths`` contains source files that the project agent explicitly
    deleted as out of scope. The authored pack remains immutable; the generated
    contract represents the adapted repository that will actually run.
    """
    if report_pack.execution is None:
        return None
    root = Path(project_root).resolve()
    excluded = {
        _normalise_relative(path, label="Excluded execution path")
        for path in excluded_paths
    }
    metadata = {
        "id": report_pack.pack_id,
        "version": report_pack.version,
        "domain": report_pack.domain,
        "manifest_sha256": report_pack.manifest_sha256,
        "source_tree_sha256": report_pack.source_tree_sha256,
    }
    contract = ExecutionContract(
        project_root=root,
        report_pack=metadata,
        working_directory=report_pack.execution.working_directory,
        render=report_pack.execution.render,
        entrypoint=report_pack.entrypoint,
        steps=tuple(
            ExecutionStep(step.step_id, step.path, step.role)
            for step in report_pack.execution.steps
            if step.path not in excluded
        ),
        artifacts=report_pack.execution.artifacts,
    )
    _validate_resolved_contract(contract)
    return contract


def write_execution_contract(
    project_root: str | Path,
    report_pack: ReportPack,
    *,
    excluded_paths: Iterable[str] = (),
) -> Path | None:
    """Write and re-parse the runtime contract for a generated project."""
    contract = execution_contract_for_pack(
        project_root,
        report_pack,
        excluded_paths=excluded_paths,
    )
    if contract is None:
        return None
    target = contract.project_root / CONTRACT_NAME
    target.write_text(
        json.dumps(contract.as_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    # The serialized artifact, rather than the in-memory object, is the runtime
    # boundary. Re-parse it immediately so generation fails on schema drift.
    load_execution_contract(contract.project_root)
    return target


def load_execution_contract(project_root: str | Path) -> ExecutionContract | None:
    """Load a generated contract, returning None only when it is absent."""
    root = Path(project_root).resolve()
    target = root / CONTRACT_NAME
    if not target.exists():
        if _pack_snapshot_declares_execution(root):
            raise ExecutionContractError(
                f"{CONTRACT_NAME} is required by {PACK_SNAPSHOT_NAME} but is missing"
            )
        return None
    if target.is_symlink() or not target.is_file():
        raise ExecutionContractError(
            f"{CONTRACT_NAME} must be a regular file"
        )
    if target.stat().st_size > MAX_CONTRACT_BYTES:
        raise ExecutionContractError(f"{CONTRACT_NAME} is too large")
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExecutionContractError(
            f"Invalid {CONTRACT_NAME}: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise ExecutionContractError(f"{CONTRACT_NAME} must contain an object")
    _strict_fields(raw, _CONTRACT_FIELDS, label="execution-contract")
    if raw["schema_version"] != CONTRACT_SCHEMA_VERSION:
        raise ExecutionContractError(
            f"Unsupported execution-contract schema: {raw['schema_version']!r}"
        )

    pack = raw["report_pack"]
    if not isinstance(pack, dict):
        raise ExecutionContractError("report_pack must be an object")
    _strict_fields(pack, _PACK_FIELDS, label="report-pack identity")
    normalised_pack: dict[str, str] = {}
    for key in ("id", "version", "domain"):
        value = pack[key]
        if not isinstance(value, str) or not value.strip():
            raise ExecutionContractError(f"report_pack.{key} must be a non-empty string")
        normalised_pack[key] = value.strip()
    for key in ("manifest_sha256", "source_tree_sha256"):
        value = pack[key]
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ExecutionContractError(f"report_pack.{key} must be a SHA-256 digest")
        normalised_pack[key] = value

    working_directory = _normalise_relative(
        raw["working_directory"],
        label="working_directory",
    )
    _resolve_project_path(
        root,
        working_directory,
        label="working_directory",
        require_directory=True,
    )
    render = raw["render"]
    if not isinstance(render, str) or render not in EXECUTION_RENDER_MODES:
        raise ExecutionContractError("render must be entrypoint or incremental")

    entrypoint_value = raw["entrypoint"]
    entrypoint: str | None
    if entrypoint_value is None:
        entrypoint = None
    else:
        entrypoint = _normalise_relative(entrypoint_value, label="entrypoint")
        entrypoint_path = _resolve_project_path(
            root,
            entrypoint,
            label="entrypoint",
            require_file=True,
        )
        if entrypoint_path.suffix.lower() != ".r":
            raise ExecutionContractError("entrypoint must be an R script")
    if render == "entrypoint" and entrypoint is None:
        raise ExecutionContractError("Entrypoint rendering requires entrypoint")

    raw_steps = raw["steps"]
    if not isinstance(raw_steps, list):
        raise ExecutionContractError("steps must be a list")
    steps: list[ExecutionStep] = []
    for raw_step in raw_steps:
        if not isinstance(raw_step, dict):
            raise ExecutionContractError("Each step must be an object")
        _strict_fields(raw_step, _STEP_FIELDS, label="execution-step")
        step_id = raw_step["id"]
        if not isinstance(step_id, str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9_-]*", step_id
        ):
            raise ExecutionContractError(f"Invalid execution step id: {step_id!r}")
        path = _normalise_relative(raw_step["path"], label=f"step {step_id} path")
        step_path = _resolve_project_path(
            root,
            path,
            label=f"step {step_id}",
            require_file=True,
        )
        if step_path.suffix.lower() != ".r":
            raise ExecutionContractError(f"Execution step is not an R script: {path}")
        role = raw_step["role"]
        if not isinstance(role, str) or role not in EXECUTABLE_FILE_ROLES:
            raise ExecutionContractError(f"Invalid role for execution step {step_id}")
        steps.append(ExecutionStep(step_id=step_id, path=path, role=role))

    if len({step.step_id for step in steps}) != len(steps):
        raise ExecutionContractError("Execution step ids must be unique")
    if len({step.path for step in steps}) != len(steps):
        raise ExecutionContractError("Execution step paths must be unique")

    raw_artifacts = raw["artifacts"]
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise ExecutionContractError("artifacts must be a non-empty list")
    artifacts = tuple(
        _normalise_relative(item, label="artifact") for item in raw_artifacts
    )
    if len(set(artifacts)) != len(artifacts):
        raise ExecutionContractError("artifacts must be unique")
    if not any(Path(path).suffix.lower() == ".html" for path in artifacts):
        raise ExecutionContractError("artifacts must include an HTML report")
    for artifact in artifacts:
        _resolve_project_path(root, artifact, label="artifact")
    contract = ExecutionContract(
        project_root=root,
        report_pack=normalised_pack,
        working_directory=working_directory,
        render=render,
        entrypoint=entrypoint,
        steps=tuple(steps),
        artifacts=artifacts,
    )
    _validate_resolved_contract(contract)
    return contract


def _validate_resolved_contract(contract: ExecutionContract) -> None:
    _resolve_project_path(
        contract.project_root,
        contract.working_directory,
        label="working_directory",
        require_directory=True,
    )
    for step in contract.steps:
        _resolve_project_path(
            contract.project_root,
            step.path,
            label=f"step {step.step_id}",
            require_file=True,
        )
    if contract.render == "entrypoint":
        if contract.entrypoint is None:
            raise ExecutionContractError("Entrypoint rendering requires entrypoint")
        _resolve_project_path(
            contract.project_root,
            contract.entrypoint,
            label="entrypoint",
            require_file=True,
        )
    for artifact in contract.artifacts:
        _resolve_project_path(
            contract.project_root,
            artifact,
            label="artifact",
        )


def _pack_snapshot_declares_execution(project_root: Path) -> bool:
    snapshot = project_root / PACK_SNAPSHOT_NAME
    if not snapshot.exists():
        return False
    if snapshot.is_symlink() or not snapshot.is_file():
        raise ExecutionContractError(f"{PACK_SNAPSHOT_NAME} must be a regular file")
    if snapshot.stat().st_size > MAX_CONTRACT_BYTES:
        raise ExecutionContractError(f"{PACK_SNAPSHOT_NAME} is too large")
    try:
        raw = yaml.safe_load(snapshot.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ExecutionContractError(f"Invalid {PACK_SNAPSHOT_NAME}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ExecutionContractError(f"{PACK_SNAPSHOT_NAME} must contain a mapping")
    return raw.get("execution") is not None
