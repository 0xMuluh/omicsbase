"""Standalone, deterministic input contract for downstream omics analysis."""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any, Iterable

CONTRACT_VERSION = "1.0"
TABULAR_FORMATS = {"csv", "tsv", "excel"}


def build_input_contract(records: Iterable[Any]) -> dict[str, Any]:
    """Validate uploaded records without executing R or mutating the study.

    The contract is standalone: it can be requested before an analysis plan
    exists and can be used by Notes, Workspace, jobs, or an external validator.
    Large/native formats are represented from safe inspector summaries and are
    marked for native validation during the actual analysis job.
    """
    tables: list[dict[str, Any]] = []
    validations: list[dict[str, str]] = []
    for record in records:
        table = _inspect_record(record)
        validations.extend(table.pop("validations", []))
        tables.append(table)

    feature_tables = [
        table for table in tables
        if table.get("role") == "feature_table" or table.get("inferred_role") == "feature_table"
    ]
    metadata_tables = [
        table for table in tables
        if table.get("role") == "metadata" or table.get("inferred_role") == "metadata"
    ]
    taxonomy_tables = [table for table in tables if table.get("role") == "taxonomy"]

    if not feature_tables:
        validations.append(_validation(
            "feature_table_missing",
            "error",
            "No feature, count, abundance, or assay table was identified.",
        ))
    if not metadata_tables:
        validations.append(_validation(
            "metadata_missing",
            "warning",
            "No sample metadata table was identified; grouping and covariate validation is incomplete.",
        ))

    joins: list[dict[str, Any]] = []
    for feature in feature_tables:
        for metadata in metadata_tables:
            joins.append(_sample_join(feature, metadata, validations))
    for taxonomy in taxonomy_tables:
        for feature in feature_tables:
            _feature_join(taxonomy, feature, validations)

    domain = _infer_domain(tables)
    groups = _grouping_candidates(metadata_tables)
    if metadata_tables and not groups:
        validations.append(_validation(
            "grouping_variable_missing",
            "warning",
            "Metadata has no validated categorical variable with at least two levels.",
        ))
    if feature_tables and not any(
        table.get("orientation") == "features_by_samples" for table in feature_tables
    ):
        validations.append(_validation(
            "feature_orientation_unresolved",
            "warning",
            "Feature-table orientation could not be confirmed from the supplied metadata.",
        ))

    error_count = sum(item["severity"] == "error" for item in validations)
    warning_count = sum(item["severity"] == "warning" for item in validations)
    status = "invalid" if error_count else ("needs_input" if warning_count else "ready")
    return {
        "version": CONTRACT_VERSION,
        "status": status,
        "domain": domain,
        "summary": {
            "table_count": len(tables),
            "feature_table_count": len(feature_tables),
            "metadata_table_count": len(metadata_tables),
            "taxonomy_table_count": len(taxonomy_tables),
            "join_count": len(joins),
            "error_count": error_count,
            "warning_count": warning_count,
        },
        "required": {
            "feature_table": bool(feature_tables),
            "metadata_table": bool(metadata_tables),
            "sample_key": bool(joins and all(join.get("status") == "valid" for join in joins)),
            "feature_key": bool(
                feature_tables and all(table.get("feature_id_column") for table in feature_tables)
            ),
        },
        "tables": tables,
        "joins": joins,
        "grouping_candidates": groups,
        "validations": validations,
    }


def _inspect_record(record: Any) -> dict[str, Any]:
    summary = _get(record, "file_summary") or {}
    name = str(_get(record, "original_name") or summary.get("name") or "unnamed")
    role = str(_get(record, "file_role") or "other")
    detected_format = str(_get(record, "detected_format") or summary.get("format") or "unknown")
    path_value = _get(record, "file_path")
    path = Path(str(path_value)) if path_value else None
    columns = [str(value) for value in (summary.get("columns") or [])]
    inferred_role = _infer_role(name, role, columns, detected_format)
    table: dict[str, Any] = {
        "id": str(_get(record, "id") or ""),
        "name": name,
        "role": role,
        "inferred_role": inferred_role,
        "format": detected_format,
        "validation_mode": "summary",
        "dimensions": summary.get("dimensions") or {},
        "columns": columns,
        "feature_id_column": None,
        "sample_id_column": None,
        "numeric_columns": [],
        "sample_ids": [],
        "feature_ids": [],
        "duplicate_ids": [],
        "orientation": "unknown",
        "categorical_summary": summary.get("categorical_summary") or {},
        "validations": [],
    }

    if path and path.exists() and detected_format in {"csv", "tsv"}:
        table.update(_read_delimited(path, detected_format == "tsv", columns))
        table["validation_mode"] = "sampled_full_header"
    else:
        table["feature_id_column"] = _first_id_column(columns, prefer_feature=True)
        table["sample_id_column"] = _first_id_column(columns, prefer_feature=False)
        table["numeric_columns"] = [
            column for column, kind in (summary.get("column_types") or {}).items()
            if str(kind).lower() in {"numeric", "integer", "double", "number"}
        ]
        table["sample_ids"] = _summary_sample_ids(summary, table)
        table["feature_ids"] = _summary_feature_ids(summary, table)
        if detected_format not in TABULAR_FORMATS:
            table["validation_mode"] = "native_job_validation"
            table["validations"].append(_validation(
                "native_validation_deferred",
                "warning",
                f"{name} requires native validation during the analysis job because {detected_format} is not a delimited table.",
            ))

    if not table["columns"] and detected_format in TABULAR_FORMATS:
        table["validations"].append(_validation(
            "columns_missing",
            "error",
            f"{name} has no readable columns.",
        ))
    if table["duplicate_ids"]:
        table["validations"].append(_validation(
            "duplicate_identifier",
            "error",
            f"{name} contains duplicate identifiers: {table['duplicate_ids'][:5]}.",
        ))
    if table["orientation"] == "features_by_samples" and table.get("negative_values"):
        table["validations"].append(_validation(
            "negative_abundance",
            "error",
            f"{name} contains negative feature values.",
        ))
    return table


def _read_delimited(path: Path, tab: bool, columns: list[str]) -> dict[str, Any]:
    delimiter = "	" if tab else ","
    sample_rows: list[dict[str, str]] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            actual_columns = [str(value or "") for value in (reader.fieldnames or columns)]
            for row_index, row in enumerate(reader):
                sample_rows.append({
                    str(key): str(value or "").strip()
                    for key, value in row.items()
                    if key is not None
                })
                if row_index >= 999:
                    break
    except (OSError, UnicodeError, csv.Error):
        return {"validations": [
            _validation("read_failed", "error", f"Could not read delimited table {path.name}.")
        ]}

    columns = actual_columns if actual_columns else columns
    feature_id_column = _first_id_column(columns, prefer_feature=True) or (
        columns[0] if columns else None
    )
    sample_id_column = _first_id_column(columns, prefer_feature=False)
    column_values = {
        column: [row.get(column, "") for row in sample_rows] for column in columns
    }
    numeric_columns: list[str] = []
    negative_values: list[float] = []
    for column, values in column_values.items():
        nonempty = [value for value in values if str(value).strip()]
        numeric = [_to_float(value) for value in nonempty]
        numeric = [value for value in numeric if value is not None]
        if numeric and len(numeric) >= max(1, int(len(nonempty) * 0.8)):
            numeric_columns.append(column)
            negative_values.extend(value for value in numeric if value < 0)

    feature_ids = _unique_nonempty(column_values.get(feature_id_column or "", []))
    sample_ids: list[str] = []
    orientation = "unknown"
    if feature_id_column and len(numeric_columns) >= 2 and feature_id_column not in numeric_columns:
        orientation = "features_by_samples"
        sample_ids = [
            column for column in columns
            if column != feature_id_column and column in numeric_columns
        ]
    elif sample_id_column:
        sample_ids = _unique_nonempty(column_values.get(sample_id_column, []))

    duplicate_ids = _duplicates(
        column_values.get(feature_id_column or sample_id_column or "", [])
    )
    categorical_summary: dict[str, list[str]] = {}
    for column, values in column_values.items():
        unique = _unique_nonempty(values)
        if 1 < len(unique) <= 20:
            categorical_summary[column] = unique
    return {
        "columns": columns,
        "feature_id_column": feature_id_column if orientation == "features_by_samples" else None,
        "sample_id_column": sample_id_column if orientation != "features_by_samples" else None,
        "numeric_columns": numeric_columns,
        "sample_ids": sample_ids,
        "feature_ids": feature_ids,
        "duplicate_ids": duplicate_ids,
        "orientation": orientation,
        "negative_values": negative_values[:20],
        "categorical_summary": categorical_summary,
        "sampled_rows": len(sample_rows),
    }


def _sample_join(
    feature: dict[str, Any],
    metadata: dict[str, Any],
    validations: list[dict[str, str]],
) -> dict[str, Any]:
    feature_ids = {
        str(value) for value in feature.get("sample_ids") or [] if str(value).strip()
    }
    metadata_ids = {
        str(value) for value in metadata.get("sample_ids") or [] if str(value).strip()
    }
    if not feature_ids or not metadata_ids:
        return {
            "feature_table": feature["name"],
            "metadata_table": metadata["name"],
            "status": "unresolved",
            "overlap_count": None,
            "feature_sample_count": len(feature_ids) or None,
            "metadata_sample_count": len(metadata_ids) or None,
        }
    overlap = feature_ids & metadata_ids
    if not overlap:
        validations.append(_validation(
            "sample_key_no_overlap",
            "error",
            f"No sample identifiers overlap between {feature['name']} and {metadata['name']}.",
        ))
        status = "invalid"
    elif overlap != feature_ids or overlap != metadata_ids:
        validations.append(_validation(
            "sample_key_partial_overlap",
            "warning",
            f"Sample identifiers only partially overlap between {feature['name']} and {metadata['name']}.",
        ))
        status = "partial"
    else:
        status = "valid"
    return {
        "feature_table": feature["name"],
        "metadata_table": metadata["name"],
        "status": status,
        "overlap_count": len(overlap),
        "feature_sample_count": len(feature_ids),
        "metadata_sample_count": len(metadata_ids),
    }


def _feature_join(
    taxonomy: dict[str, Any],
    feature: dict[str, Any],
    validations: list[dict[str, str]],
) -> None:
    tax_ids = {str(value) for value in taxonomy.get("feature_ids") or [] if str(value).strip()}
    feature_ids = {
        str(value) for value in feature.get("feature_ids") or [] if str(value).strip()
    }
    if tax_ids and feature_ids and not tax_ids & feature_ids:
        validations.append(_validation(
            "feature_key_no_overlap",
            "warning",
            f"Taxonomy identifiers do not overlap the feature identifiers in {feature['name']}.",
        ))


def _grouping_candidates(metadata_tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for table in metadata_tables:
        sample_column = table.get("sample_id_column")
        for column in table.get("columns") or []:
            if column == sample_column or column in table.get("numeric_columns", []):
                continue
            values = (table.get("categorical_summary") or {}).get(column) or []
            if len(values) >= 2:
                candidates.append({
                    "table": table["name"],
                    "column": column,
                    "levels": [str(value) for value in values[:20]],
                    "confidence": "high" if len(values) <= 8 else "medium",
                })
    return candidates


def _summary_sample_ids(summary: dict[str, Any], table: dict[str, Any]) -> list[str]:
    preview = summary.get("preview_rows") or []
    column = table.get("sample_id_column")
    if not column or column not in table.get("columns", []):
        return []
    index = table["columns"].index(column)
    return _unique_nonempty([
        row[index] for row in preview
        if isinstance(row, (list, tuple)) and len(row) > index
    ])


def _summary_feature_ids(summary: dict[str, Any], table: dict[str, Any]) -> list[str]:
    preview = summary.get("preview_rows") or []
    column = table.get("feature_id_column")
    if not column or column not in table.get("columns", []):
        return []
    index = table["columns"].index(column)
    return _unique_nonempty([
        row[index] for row in preview
        if isinstance(row, (list, tuple)) and len(row) > index
    ])


def _infer_role(name: str, role: str, columns: list[str], detected_format: str) -> str:
    if role in {"feature_table", "metadata", "taxonomy"}:
        return role
    searchable = " ".join([name, role, detected_format, *columns]).lower()
    if any(term in searchable for term in ("taxonomy", "taxon", "otu", "asv", "biom")):
        return "feature_table"
    if any(term in searchable for term in ("metadata", "sample", "phenotype", "clinical", "coldata")):
        return "metadata"
    if any(term in searchable for term in ("metabol", "abundance", "count", "feature", "assay")):
        return "feature_table"
    return "other"


def _infer_domain(tables: list[dict[str, Any]]) -> str:
    searchable = " ".join(
        f"{table.get('name', '')} {table.get('role', '')} {table.get('format', '')} "
        f"{' '.join(table.get('columns') or [])}"
        for table in tables
    ).lower()
    microbiome = ("microbiome", "microbiota", "taxonomy", "taxon", "otu", "asv", "biom", "qiime")
    metabolomics = ("metabol", "lipid", "nmr", "serum", "compound", "peak", "mz")
    microbiome_score = sum(term in searchable for term in microbiome)
    metabolomics_score = sum(term in searchable for term in metabolomics)
    if microbiome_score == metabolomics_score:
        return "unknown"
    return "microbiome" if microbiome_score > metabolomics_score else "metabolomics"


def _first_id_column(columns: list[str], *, prefer_feature: bool) -> str | None:
    for column in columns:
        normalized = _normalise(column)
        if prefer_feature and any(
            term in normalized for term in ("feature", "taxon", "metabol", "compound", "otu", "asv")
        ):
            return column
        if not prefer_feature and any(
            term in normalized for term in ("sample", "subject", "participant", "patient", "specimen")
        ):
            return column
    for column in columns:
        if _normalise(column) in {"id", "sampleid", "featureid"}:
            return column
    return None


def _normalise(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _to_float(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _unique_nonempty(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _duplicates(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        if text in seen and text not in duplicates:
            duplicates.append(text)
        seen.add(text)
    return duplicates[:20]


def _get(record: Any, name: str) -> Any:
    if isinstance(record, dict):
        return record.get(name)
    return getattr(record, name, None)


def _validation(code: str, severity: str, message: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "message": message}


__all__ = ["CONTRACT_VERSION", "build_input_contract"]

