"""Build a structural, analyst-facing contract from uploaded study files."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from app.services.omics_contract import build_input_contract


MANIFEST_VERSION = "1.0"
DATA_ROLES = {"feature_table", "metadata", "taxonomy", "other"}
ID_NAME_HINTS = ("id", "sample", "subject", "participant", "patient", "feature", "otu", "asv", "tax")


def build_study_manifest(files: Iterable[Any]) -> dict[str, Any]:
    """Compile uploaded-file records into a stable planning and validation contract."""
    records = list(files)
    manifest_files: list[dict[str, Any]] = []
    roles: dict[str, list[str]] = {}
    grouping_candidates: list[dict[str, Any]] = []
    identifier_candidates: list[dict[str, Any]] = []
    validations: list[dict[str, str]] = []

    for record in records:
        summary = _value(record, "file_summary") or {}
        role = _value(record, "file_role") or "other"
        name = _value(record, "original_name") or summary.get("name") or "unnamed"
        detected_format = _value(record, "detected_format") or summary.get("format") or "unknown"
        columns = list(summary.get("columns") or [])
        dimensions = summary.get("dimensions") or {}

        roles.setdefault(role, []).append(name)
        manifest_files.append(
            {
                "id": str(_value(record, "id") or ""),
                "name": name,
                "role": role,
                "format": detected_format,
                "dimensions": dimensions,
                "columns": columns,
                "inspection_status": "error" if detected_format == "error" else "inspected",
            }
        )

        if detected_format == "error":
            validations.append(
                _validation(
                    "inspection_failed",
                    "error",
                    f"{name} could not be inspected: {summary.get('error', 'unknown inspection error')}",
                )
            )
        elif detected_format == "unknown" and role != "analysis_plan":
            validations.append(
                _validation("unsupported_format", "error", f"{name} has an unsupported or unknown format.")
            )

        for column in columns:
            normalized = column.lower().replace("-", "_").replace(" ", "_")
            if any(hint in normalized for hint in ID_NAME_HINTS):
                identifier_candidates.append(
                    {"file": name, "column": column, "role": role, "confidence": "medium"}
                )

        for column, levels in (summary.get("categorical_summary") or {}).items():
            if not isinstance(levels, list) or not 2 <= len(levels) <= 20:
                continue
            normalized = column.lower().replace("-", "_").replace(" ", "_")
            if any(hint in normalized for hint in ID_NAME_HINTS):
                continue
            grouping_candidates.append(
                {
                    "file": name,
                    "column": column,
                    "levels": levels,
                    "role": role,
                    "confidence": "high" if role == "metadata" and len(levels) <= 8 else "medium",
                }
            )

    data_files = [item for item in manifest_files if item["role"] in DATA_ROLES]
    recognized_data_files = [
        item for item in data_files if item["format"] not in {"unknown", "error", "text"}
    ]
    domain_scores = _score_domains(manifest_files)
    ranked_domains = sorted(domain_scores.items(), key=lambda item: item[1], reverse=True)
    domain = ranked_domains[0][0] if ranked_domains and ranked_domains[0][1] >= 2 else "unknown"
    if len(ranked_domains) > 1 and ranked_domains[0][1] == ranked_domains[1][1]:
        domain = "unknown"

    if not data_files:
        validations.append(
            _validation(
                "missing_data",
                "error",
                "Upload at least one supported study data file before planning.",
            )
        )
    elif not recognized_data_files:
        validations.append(
            _validation(
                "missing_supported_data",
                "error",
                "No supported study data file was detected.",
            )
        )

    if recognized_data_files and not roles.get("metadata") and not grouping_candidates:
        validations.append(
            _validation(
                "metadata_not_identified",
                "warning",
                "No metadata table was identified; group comparisons may require clarification.",
            )
        )

    if recognized_data_files and not grouping_candidates:
        validations.append(
            _validation(
                "grouping_variable_unresolved",
                "warning",
                "No categorical grouping variable was detected automatically.",
            )
        )

    if recognized_data_files and domain == "unknown":
        validations.append(
            _validation(
                "analysis_domain_unresolved",
                "warning",
                "The uploaded files do not clearly identify microbiome or metabolomics data.",
            )
        )

    input_contract = build_input_contract(records)
    contract_validations = input_contract.get("validations") or []
    all_validations = [*validations, *contract_validations]
    error_count = sum(item["severity"] == "error" for item in all_validations)
    warning_count = sum(item["severity"] == "warning" for item in all_validations)
    status = "invalid" if error_count else ("needs_input" if warning_count else "ready")

    return {
        "version": MANIFEST_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "domain": domain,
        "domain_candidates": [
            {"domain": candidate, "score": score}
            for candidate, score in ranked_domains
            if score > 0
        ],
        "summary": {
            "file_count": len(manifest_files),
            "data_file_count": len(data_files),
            "recognized_data_file_count": len(recognized_data_files),
            "error_count": error_count,
            "warning_count": warning_count,
        },
        "files": manifest_files,
        "roles": roles,
        "identifier_candidates": identifier_candidates,
        "grouping_candidates": grouping_candidates,
        "validations": validations,
        "input_contract": input_contract,
    }


def manifest_errors(
    manifest: dict[str, Any] | None,
    *,
    include_input_contract: bool = True,
) -> list[str]:
    """Return blocking validation messages from a manifest.

    Before LLM classification, callers may omit the nested contract because
    its role-based checks are intentionally unresolved. After classification,
    the nested contract is authoritative and its errors are blocking too.
    """
    if not manifest:
        return ["Study manifest has not been created."]
    messages: list[str] = []
    seen: set[str] = set()
    validations = list(manifest.get("validations", []))
    if include_input_contract:
        contract = manifest.get("input_contract") or {}
        validations.extend(contract.get("validations") or [])
    for item in validations:
        if item.get("severity") != "error":
            continue
        message = item.get("message", "Invalid study input")
        if message not in seen:
            seen.add(message)
            messages.append(message)
    return messages


def format_manifest_for_llm(manifest: dict[str, Any] | None) -> str:
    """Render the stable, relevant subset of a manifest for planning prompts."""
    if not manifest:
        return "(No study manifest available)"

    lines = [
        f"Status: {manifest.get('status', 'unknown')}",
        f"Detected domain: {manifest.get('domain', 'unknown')}",
        "Files:",
    ]
    for item in manifest.get("files", []):
        lines.append(
            f"- {item.get('name')}: role={item.get('role')}, format={item.get('format')}, "
            f"dimensions={item.get('dimensions')}, columns={item.get('columns')}"
        )

    candidates = manifest.get("grouping_candidates", [])
    if candidates:
        lines.append("Candidate grouping variables:")
        for candidate in candidates:
            lines.append(
                f"- {candidate.get('file')}::{candidate.get('column')} "
                f"levels={candidate.get('levels')} confidence={candidate.get('confidence')}"
            )

    validations = manifest.get("validations", [])
    if validations:
        lines.append("Structural validation findings:")
        for validation in validations:
            lines.append(
                f"- [{validation.get('severity')}] {validation.get('message')}"
            )
    contract = manifest.get("input_contract") or {}
    if contract:
        lines.append(
            "Structural input contract: "
            f"status={contract.get('status', 'unknown')}, "
            f"required={contract.get('required', {})}"
        )
        for validation in contract.get("validations") or []:
            lines.append(
                f"- [contract:{validation.get('severity')}] {validation.get('message')}"
            )
    return "\n".join(lines)


def _value(record: Any, name: str) -> Any:
    if isinstance(record, dict):
        return record.get(name)
    return getattr(record, name, None)


def _validation(code: str, severity: str, message: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "message": message}


def _score_domains(files: list[dict[str, Any]]) -> dict[str, int]:
    scores = {"microbiome": 0, "metabolomics": 0}
    microbiome_terms = (
        "microbiome", "microbiota", "metaphlan", "humann", "taxonomy",
        "taxa", "otu", "asv", "abundance", "counts", "biom",
    )
    metabolomics_terms = (
        "metabolomics", "metabolite", "metabolites", "nmr", "serum",
        "biomarker", "lipid", "amino_acid",
    )

    for item in files:
        searchable = " ".join(
            [
                str(item.get("name", "")),
                str(item.get("role", "")),
                " ".join(str(column) for column in item.get("columns", [])),
            ]
        ).lower()
        detected_format = item.get("format")
        if detected_format in {"biom", "qiime2_qza"}:
            scores["microbiome"] += 5
        scores["microbiome"] += sum(term in searchable for term in microbiome_terms)
        scores["metabolomics"] += sum(term in searchable for term in metabolomics_terms)
    return scores
