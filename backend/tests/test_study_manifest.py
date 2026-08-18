"""Tests for deterministic uploaded-study contracts."""

from types import SimpleNamespace

from app.services.study_manifest import build_study_manifest, manifest_errors


def _file(name: str, role: str, summary: dict):
    return SimpleNamespace(
        id=name,
        original_name=name,
        file_role=role,
        detected_format=summary.get("format"),
        file_summary=summary,
    )


def test_empty_manifest_blocks_planning():
    manifest = build_study_manifest([])

    assert manifest["status"] == "invalid"
    assert manifest["summary"]["data_file_count"] == 0
    assert "Upload at least one supported study data file" in manifest_errors(manifest)[0]


def test_manifest_preserves_multiple_files_per_role():
    files = [
        _file(
            "counts-a.csv",
            "feature_table",
            {"format": "csv", "dimensions": {"rows": 10, "columns": 4}, "columns": ["taxon", "S1", "S2", "S3"]},
        ),
        _file(
            "counts-b.csv",
            "feature_table",
            {"format": "csv", "dimensions": {"rows": 12, "columns": 4}, "columns": ["taxon", "S4", "S5", "S6"]},
        ),
        _file(
            "metadata.csv",
            "metadata",
            {
                "format": "csv",
                "dimensions": {"rows": 6, "columns": 2},
                "columns": ["sample_id", "condition"],
                "categorical_summary": {"condition": ["Control", "Treatment"]},
            },
        ),
    ]

    manifest = build_study_manifest(files)

    assert manifest["status"] == "needs_input"
    assert manifest["domain"] == "microbiome"
    assert manifest["roles"]["feature_table"] == ["counts-a.csv", "counts-b.csv"]
    assert manifest["grouping_candidates"][0]["column"] == "condition"
    assert manifest["grouping_candidates"][0]["confidence"] == "high"


def test_unknown_data_format_is_blocking():
    manifest = build_study_manifest(
        [_file("raw.fastq", "other", {"format": "unknown", "columns": []})]
    )

    assert manifest["status"] == "invalid"
    assert any("unsupported" in message.lower() for message in manifest_errors(manifest))


def test_manifest_detects_metabolomics_domain():
    manifest = build_study_manifest(
        [
            _file(
                "serum_metabolites.xlsx",
                "other",
                {
                    "format": "excel",
                    "dimensions": {"rows": 50, "columns": 4},
                    "columns": ["StudyID", "condition", "metabolite_A", "metabolite_B"],
                    "categorical_summary": {"condition": ["Control", "Exposed"]},
                },
            )
        ]
    )

    assert manifest["domain"] == "metabolomics"
    assert manifest["status"] == "invalid"
    assert any("No feature" in message for message in manifest_errors(manifest))


def test_manifest_can_defer_role_contract_until_after_classification():
    manifest = build_study_manifest(
        [_file(
            "counts.csv",
            "other",
            {"format": "csv", "columns": ["feature_id", "S1", "S2"]},
        )]
    )

    assert manifest_errors(manifest, include_input_contract=False) == []
    assert any("No feature" in message for message in manifest_errors(manifest))
