"""Strict report-pack classification without imposing a study-input schema.

A report pack is an existing R/Quarto directory used as a methodological
prior. Its optional manifest tells OmicsBase which source files require
adaptation. Arbitrary directories remain usable through conservative discovery:
unknown source is inspected, while only known assets are copied untouched.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import yaml


MANIFEST_NAME = "omicsbase-pack.yaml"
SUPPORTED_SCHEMA_VERSION = "1.0"
ADAPTATION_POLICIES = {"none", "inspect", "required"}
ALLOWED_FILE_ROLES = {
    "data_loader",
    "helper",
    "analysis",
    "page",
    "assembly",
    "orchestrator",
    "validator",
    "static",
}
_ROOT_FIELDS = {
    "schema_version",
    "id",
    "version",
    "domain",
    "name",
    "entrypoint",
    "default_adaptation",
    "prompt_references",
    "file_rules",
    "execution",
    "capabilities",
}
_RULE_FIELDS = {"id", "match", "role", "adaptation"}
_EXECUTION_FIELDS = {"working_directory", "render", "steps", "artifacts"}
_EXECUTION_STEP_FIELDS = {"id", "path", "role"}
_CAPABILITY_FIELDS = {"sources", "execution_steps", "parameters", "outputs", "validators"}
EXECUTION_RENDER_MODES = {"entrypoint", "incremental"}
EXECUTABLE_FILE_ROLES = {"data_loader", "analysis", "validator"}
_KNOWN_STATIC_SUFFIXES = {
    ".css",
    ".js",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
}
_SOURCE_SKIP_DIRS = {
    "_freeze",
    ".quarto",
    "__pycache__",
    "site_libs",
    "data",
    "output",
    "outputs",
    "lib",
    "node_modules",
    ".git",
    ".venv",
}
_SOURCE_SKIP_FILENAMES = {MANIFEST_NAME, ".rhistory", ".rdata"}


class ReportPackError(ValueError):
    """Raised when a report-pack manifest is unsafe or internally invalid."""


def _normalise_relative(value: str, *, label: str) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise ReportPackError(f"{label} must be a safe relative path: {value!r}")
    return path.as_posix()


def _strict_fields(value: dict[str, Any], allowed: set[str], *, label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ReportPackError(f"Unknown {label} field(s): {', '.join(unknown)}")


def report_pack_source_files(root: str | Path) -> list[Path]:
    """Return the complete safe source inventory, excluding data and secrets."""
    pack_root = Path(root).resolve()
    files: list[Path] = []
    for path in sorted(pack_root.rglob("*")):
        relative = path.relative_to(pack_root)
        if path.is_symlink():
            raise ReportPackError(
                f"Report-pack source may not be a symlink: {relative.as_posix()}"
            )
        if not path.is_file():
            continue
        if any(part in _SOURCE_SKIP_DIRS for part in relative.parts):
            continue
        name = path.name.lower()
        if (
            name in _SOURCE_SKIP_FILENAMES
            or name == ".env"
            or name.startswith(".env.")
            or "credential" in name
            or name.endswith((".pem", ".key"))
        ):
            continue
        try:
            path.resolve().relative_to(pack_root)
        except ValueError as exc:
            raise ReportPackError(
                f"Report-pack source escaped its root: {relative.as_posix()}"
            ) from exc
        files.append(path)
    return files


def _source_tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in report_pack_source_files(root):
        relative = path.relative_to(root.resolve()).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


_R_SOURCE_LITERAL = re.compile(
    r"""\bsource\s*\(\s*["']([^"']+)["']""",
    re.IGNORECASE,
)


def validate_source_closure(
    root: str | Path,
    *,
    execution_working_directory: str | None = None,
) -> None:
    """Reject literal R source() references that escape or miss the pack."""
    pack_root = Path(root).resolve()
    code_root = pack_root / "code"
    execution_root = (
        pack_root / execution_working_directory
        if execution_working_directory is not None
        else None
    )
    execute_from_project = False
    quarto_path = code_root / "_quarto.yml"
    if quarto_path.is_file():
        try:
            quarto = yaml.safe_load(quarto_path.read_text()) or {}
            execute_from_project = (
                str((quarto.get("project") or {}).get("execute-dir") or "").lower()
                == "project"
            )
        except yaml.YAMLError as exc:
            raise ReportPackError(f"Invalid Quarto YAML in {quarto_path}: {exc}") from exc

    for source_file in report_pack_source_files(pack_root):
        if source_file.suffix.lower() not in {".r", ".qmd", ".rmd"}:
            continue
        text = source_file.read_text(errors="replace")
        for reference in _R_SOURCE_LITERAL.findall(text):
            source_ref = Path(reference)
            if source_ref.is_absolute():
                raise ReportPackError(
                    f"Absolute source dependency in {source_file.relative_to(pack_root)}: "
                    f"{reference}"
                )
            if source_file.suffix.lower() in {".qmd", ".rmd"}:
                base = code_root if execute_from_project else source_file.parent
            elif execution_root is not None:
                base = execution_root
            elif code_root in source_file.parents:
                base = code_root
            else:
                base = source_file.parent
            resolved = (base / source_ref).resolve()
            try:
                resolved.relative_to(pack_root)
            except ValueError as exc:
                raise ReportPackError(
                    f"Source dependency escaped the pack in "
                    f"{source_file.relative_to(pack_root)}: {reference}"
                ) from exc
            if not resolved.is_file():
                raise ReportPackError(
                    f"Missing source dependency in {source_file.relative_to(pack_root)}: "
                    f"{reference}"
                )


@dataclass(frozen=True)
class ReportPackRule:
    rule_id: str
    match: str
    role: str
    adaptation: str

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "ReportPackRule":
        if not isinstance(value, dict):
            raise ReportPackError("Each report-pack file rule must be a mapping")
        _strict_fields(value, _RULE_FIELDS, label="file-rule")
        rule_id = str(value.get("id") or "").strip()
        if not rule_id:
            raise ReportPackError("Each report-pack file rule requires an id")
        match = _normalise_relative(value.get("match", ""), label=f"Match for {rule_id}")
        role = str(value.get("role") or "").strip().lower()
        if role not in ALLOWED_FILE_ROLES:
            raise ReportPackError(
                f"Unknown report-pack file role {role!r}; expected one of "
                f"{', '.join(sorted(ALLOWED_FILE_ROLES))}"
            )
        adaptation = str(value.get("adaptation") or "").strip().lower()
        if adaptation not in ADAPTATION_POLICIES:
            raise ReportPackError(
                f"Unknown adaptation policy {adaptation!r}; expected none, inspect, or required"
            )
        return cls(rule_id=rule_id, match=match, role=role, adaptation=adaptation)

    def matches(self, relative_path: str) -> bool:
        path = PurePosixPath(relative_path)
        return path.match(self.match) or fnmatch.fnmatchcase(relative_path, self.match)


@dataclass(frozen=True)
class ReportPackFile:
    path: str
    role: str
    adaptation: str
    matched_rule_id: str | None = None
    classification_source: str = "discovered"

    @property
    def study_dependent(self) -> bool:
        return self.adaptation != "none"

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "role": self.role,
            "adaptation": self.adaptation,
            "study_dependent": self.study_dependent,
            "matched_rule_id": self.matched_rule_id,
            "classification_source": self.classification_source,
        }


@dataclass(frozen=True)
class ReportPackExecutionStep:
    step_id: str
    path: str
    role: str

    def as_dict(self) -> dict[str, str]:
        return {"id": self.step_id, "path": self.path, "role": self.role}


@dataclass(frozen=True)
class ReportPackExecution:
    working_directory: str
    render: str
    steps: tuple[ReportPackExecutionStep, ...] = ()
    artifacts: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "working_directory": self.working_directory,
            "render": self.render,
            "steps": [step.as_dict() for step in self.steps],
            "artifacts": list(self.artifacts),
        }


@dataclass(frozen=True)
class ReportPackCapability:
    """A named scientific capability exposed by a ReportPack."""

    capability_id: str
    sources: tuple[str, ...] = ()
    execution_steps: tuple[str, ...] = ()
    parameters: dict[str, Any] = field(default_factory=dict)
    outputs: tuple[str, ...] = ()
    validators: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.capability_id,
            "sources": list(self.sources),
            "execution_steps": list(self.execution_steps),
            "parameters": dict(self.parameters),
            "outputs": list(self.outputs),
            "validators": list(self.validators),
        }


@dataclass
class ReportPack:
    root: Path
    pack_id: str
    version: str
    domain: str
    name: str
    schema_version: str = SUPPORTED_SCHEMA_VERSION
    entrypoint: str | None = None
    default_adaptation: str = "inspect"
    rules: tuple[ReportPackRule, ...] = ()
    prompt_references: tuple[str, ...] = ()
    execution: ReportPackExecution | None = None
    capabilities: tuple[ReportPackCapability, ...] = ()
    source: str = "discovered"
    manifest_sha256: str = ""
    source_tree_sha256: str = ""
    raw_manifest: dict[str, Any] = field(default_factory=dict, repr=False)

    def classify(self, relative_path: str) -> ReportPackFile:
        relative = _normalise_relative(relative_path, label="Report-pack file")
        for rule in self.rules:
            if rule.matches(relative):
                return ReportPackFile(
                    path=relative,
                    role=rule.role,
                    adaptation=rule.adaptation,
                    matched_rule_id=rule.rule_id,
                    classification_source="declared",
                )
        role, discovered_policy = _discover_classification(relative)
        return ReportPackFile(
            path=relative,
            role=role,
            adaptation=(
                discovered_policy
                if discovered_policy in {"none", "required"}
                else self.default_adaptation
            ),
            classification_source="discovered",
        )

    def inventory(self, relative_paths: Iterable[str] | None = None) -> list[ReportPackFile]:
        paths = relative_paths
        if paths is None:
            paths = (
                path.relative_to(self.root).as_posix()
                for path in report_pack_source_files(self.root)
            )
        return [self.classify(path) for path in paths]

    def metadata(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.pack_id,
            "version": self.version,
            "domain": self.domain,
            "name": self.name,
            "entrypoint": self.entrypoint,
            "default_adaptation": self.default_adaptation,
            "source": self.source,
            "manifest_sha256": self.manifest_sha256,
            "source_tree_sha256": self.source_tree_sha256,
            "prompt_references": list(self.prompt_references),
            "execution": self.execution.as_dict() if self.execution else None,
            "capabilities": [capability.as_dict() for capability in self.capabilities],
        }

    def resolved_inventory(self, relative_paths: Iterable[str]) -> list[dict[str, Any]]:
        return [item.as_dict() for item in self.inventory(relative_paths)]


def _discover_classification(relative_path: str) -> tuple[str, str]:
    path = PurePosixPath(relative_path)
    name = path.name.lower()
    suffix = path.suffix.lower()
    if name == "_quarto.yml":
        return "assembly", "inspect"
    if suffix in {".qmd", ".rmd"}:
        return "page", "inspect"
    if name in {"data.r", "make_mae.r", "load_data.r", "prepare_data.r"}:
        return "data_loader", "required"
    if name == "main.r":
        return "orchestrator", "inspect"
    if suffix == ".r":
        return "analysis", "inspect"
    if suffix in _KNOWN_STATIC_SUFFIXES:
        return "static", "none"
    # Unknown material is not assumed static. The agent must inspect it unless
    # a declared first-match rule says it is safe to copy.
    return "static", "inspect"


def _parse_execution(
    value: Any,
    *,
    pack_root: Path,
    entrypoint: str | None,
) -> ReportPackExecution | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ReportPackError("Report-pack execution must be a mapping")
    _strict_fields(value, _EXECUTION_FIELDS, label="execution")

    working_directory = _normalise_relative(
        value.get("working_directory", ""),
        label="Execution working_directory",
    )
    working_path = (pack_root / working_directory).resolve()
    try:
        working_path.relative_to(pack_root)
    except ValueError as exc:
        raise ReportPackError("Execution working_directory escaped the report pack") from exc
    if not working_path.is_dir():
        raise ReportPackError(
            f"Execution working_directory does not exist: {working_directory}"
        )

    render = str(value.get("render") or "").strip().lower()
    if render not in EXECUTION_RENDER_MODES:
        raise ReportPackError("Execution render must be entrypoint or incremental")
    if render == "entrypoint":
        if entrypoint is None:
            raise ReportPackError("Entrypoint rendering requires a pack entrypoint")
        if Path(entrypoint).suffix.lower() != ".r":
            raise ReportPackError("Report-pack execution entrypoint must be an R script")

    raw_steps = value.get("steps", [])
    if not isinstance(raw_steps, list):
        raise ReportPackError("Execution steps must be a list")
    steps: list[ReportPackExecutionStep] = []
    for raw_step in raw_steps:
        if not isinstance(raw_step, dict):
            raise ReportPackError("Each execution step must be a mapping")
        _strict_fields(raw_step, _EXECUTION_STEP_FIELDS, label="execution-step")
        step_id = str(raw_step.get("id") or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", step_id):
            raise ReportPackError(
                "Execution step id must use lowercase letters, digits, hyphens, or underscores"
            )
        path = _normalise_relative(
            raw_step.get("path", ""),
            label=f"Path for execution step {step_id}",
        )
        source_path = (pack_root / path).resolve()
        try:
            source_path.relative_to(pack_root)
        except ValueError as exc:
            raise ReportPackError(
                f"Execution step escaped the report pack: {path}"
            ) from exc
        if not source_path.is_file() or source_path.suffix.lower() != ".r":
            raise ReportPackError(
                f"Execution step must reference an existing R script: {path}"
            )
        role = str(raw_step.get("role") or "").strip().lower()
        if role not in EXECUTABLE_FILE_ROLES:
            raise ReportPackError(
                f"Execution step role {role!r} must be data_loader, analysis, or validator"
            )
        steps.append(ReportPackExecutionStep(step_id=step_id, path=path, role=role))

    step_ids = [step.step_id for step in steps]
    step_paths = [step.path for step in steps]
    if len(step_ids) != len(set(step_ids)):
        raise ReportPackError("Execution step ids must be unique")
    if len(step_paths) != len(set(step_paths)):
        raise ReportPackError("Execution step paths must be unique")

    raw_artifacts = value.get("artifacts")
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise ReportPackError("Execution artifacts must be a non-empty list")
    artifacts = tuple(
        _normalise_relative(item, label="Execution artifact")
        for item in raw_artifacts
    )
    if len(artifacts) != len(set(artifacts)):
        raise ReportPackError("Execution artifacts must be unique")
    if not any(Path(path).suffix.lower() == ".html" for path in artifacts):
        raise ReportPackError("Execution artifacts must include an HTML report")
    return ReportPackExecution(
        working_directory=working_directory,
        render=render,
        steps=tuple(steps),
        artifacts=artifacts,
    )


def _parse_capabilities(
    value: Any,
    *,
    pack_root: Path,
    execution: ReportPackExecution | None,
) -> tuple[ReportPackCapability, ...]:
    """Parse named capability bindings and derive a safe default when absent."""
    if value is None:
        if execution is None:
            return ()
        validators = tuple(step.path for step in execution.steps if step.role == "validator")
        return (
            ReportPackCapability(
                capability_id="report_execution",
                sources=tuple(step.path for step in execution.steps),
                execution_steps=tuple(step.step_id for step in execution.steps),
                outputs=execution.artifacts,
                validators=validators,
            ),
        )
    if not isinstance(value, dict):
        raise ReportPackError("Report-pack capabilities must be a mapping of id to declaration")
    capabilities: list[ReportPackCapability] = []
    step_ids = {step.step_id for step in (execution.steps if execution else ())}
    for raw_id, raw_value in value.items():
        capability_id = str(raw_id or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", capability_id):
            raise ReportPackError(f"Invalid capability id: {raw_id!r}")
        if not isinstance(raw_value, dict):
            raise ReportPackError(f"Capability {capability_id} must be a mapping")
        _strict_fields(raw_value, _CAPABILITY_FIELDS, label=f"capability {capability_id}")
        def paths(key: str) -> tuple[str, ...]:
            raw_paths = raw_value.get(key) or []
            if not isinstance(raw_paths, list):
                raise ReportPackError(f"Capability {capability_id}.{key} must be a list")
            result = tuple(_normalise_relative(item, label=f"Capability {capability_id}.{key}") for item in raw_paths)
            for relative in result:
                candidate = (pack_root / relative).resolve()
                try:
                    candidate.relative_to(pack_root)
                except ValueError as exc:
                    raise ReportPackError(f"Capability {capability_id}.{key} escaped the pack") from exc
                if key != "outputs" and not candidate.is_file():
                    raise ReportPackError(f"Capability {capability_id}.{key} references missing file: {relative}")
            return result
        raw_steps = raw_value.get("execution_steps") or []
        if not isinstance(raw_steps, list) or any(not isinstance(item, str) for item in raw_steps):
            raise ReportPackError(f"Capability {capability_id}.execution_steps must be a list of step ids")
        execution_steps = tuple(str(item).strip() for item in raw_steps)
        unknown_steps = sorted(set(execution_steps) - step_ids)
        if unknown_steps:
            raise ReportPackError(f"Capability {capability_id} references unknown execution step(s): {', '.join(unknown_steps)}")
        parameters = raw_value.get("parameters") or {}
        if not isinstance(parameters, dict):
            raise ReportPackError(f"Capability {capability_id}.parameters must be a mapping")
        capabilities.append(
            ReportPackCapability(
                capability_id=capability_id,
                sources=paths("sources"),
                execution_steps=execution_steps,
                parameters=dict(parameters),
                outputs=paths("outputs"),
                validators=paths("validators"),
            )
        )
    if not capabilities:
        raise ReportPackError("Report-pack capabilities must declare at least one capability")
    return tuple(capabilities)


def load_report_pack(root: str | Path, *, domain: str = "", manifest_name: str = MANIFEST_NAME) -> ReportPack:
    pack_root = Path(root).resolve()
    if not pack_root.exists() or not pack_root.is_dir():
        raise ReportPackError(f"Report-pack directory does not exist: {pack_root}")

    manifest_path = pack_root / manifest_name
    if not manifest_path.exists():
        canonical = json.dumps(
            {"root": pack_root.name, "domain": domain, "source": "discovered"},
            sort_keys=True,
        )
        return ReportPack(
            root=pack_root,
            pack_id=pack_root.name,
            version="unversioned",
            domain=domain,
            name=pack_root.name.replace("_", " ").replace("-", " ").title(),
            source="discovered",
            manifest_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            source_tree_sha256=_source_tree_digest(pack_root),
        )

    raw_bytes = manifest_path.read_bytes()
    try:
        raw = yaml.safe_load(raw_bytes) or {}
    except yaml.YAMLError as exc:
        raise ReportPackError(f"Invalid YAML in {manifest_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ReportPackError("Report-pack manifest must contain a YAML mapping")
    _strict_fields(raw, _ROOT_FIELDS, label="manifest")

    schema_version = str(raw.get("schema_version") or "").strip()
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise ReportPackError(
            f"Unsupported report-pack schema {schema_version!r}; expected {SUPPORTED_SCHEMA_VERSION}"
        )
    pack_id = str(raw.get("id") or "").strip()
    version = str(raw.get("version") or "").strip()
    manifest_domain = str(raw.get("domain") or domain or "").strip().lower()
    if not pack_id or not version or not manifest_domain:
        raise ReportPackError("Report-pack manifest requires id, version, and domain")

    default_adaptation = str(raw.get("default_adaptation") or "").strip().lower()
    if default_adaptation not in {"inspect", "required"}:
        raise ReportPackError("default_adaptation must be inspect or required")

    entrypoint = raw.get("entrypoint")
    if entrypoint is not None:
        entrypoint = _normalise_relative(str(entrypoint), label="Entrypoint")
        if not (pack_root / entrypoint).is_file():
            raise ReportPackError(f"Report-pack entrypoint does not exist: {entrypoint}")

    rules = tuple(ReportPackRule.from_mapping(item) for item in (raw.get("file_rules") or []))
    rule_ids = [rule.rule_id for rule in rules]
    if len(rule_ids) != len(set(rule_ids)):
        raise ReportPackError("Report-pack file-rule ids must be unique")
    prompt_references = tuple(
        _normalise_relative(item, label="Prompt reference")
        for item in (raw.get("prompt_references") or [])
    )
    execution = _parse_execution(
        raw.get("execution"),
        pack_root=pack_root,
        entrypoint=entrypoint,
    )
    capabilities = _parse_capabilities(
        raw.get("capabilities"),
        pack_root=pack_root,
        execution=execution,
    )
    validate_source_closure(
        pack_root,
        execution_working_directory=(
            execution.working_directory if execution is not None else None
        ),
    )

    pack = ReportPack(
        root=pack_root,
        pack_id=pack_id,
        version=version,
        domain=manifest_domain,
        name=str(raw.get("name") or pack_id).strip(),
        schema_version=schema_version,
        entrypoint=entrypoint,
        default_adaptation=default_adaptation,
        rules=rules,
        prompt_references=prompt_references,
        execution=execution,
        capabilities=capabilities,
        source="declared",
        manifest_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        source_tree_sha256=_source_tree_digest(pack_root),
        raw_manifest=raw,
    )
    if execution is not None:
        for step in execution.steps:
            classified = pack.classify(step.path)
            if classified.role != step.role:
                raise ReportPackError(
                    f"Execution step {step.step_id} declares role {step.role!r}, but "
                    f"file classification resolves to {classified.role!r}"
                )
        if execution.render == "entrypoint" and entrypoint is not None:
            classified_entrypoint = pack.classify(entrypoint)
            if classified_entrypoint.role != "orchestrator":
                raise ReportPackError(
                    "Report-pack entrypoint must classify as an orchestrator"
                )
    return pack
