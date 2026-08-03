"""Tests for the standalone deterministic omics input contract."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.omics_contract import build_input_contract


def _record(name: str, role: str, path: str):
    return SimpleNamespace(
        id=name,
        original_name=name,
        file_role=role,
        detected_format="csv",
        file_path=path,
        file_summary={},
    )


def test_contract_validates_feature_metadata_sample_join(tmp_path):
    features = tmp_path / "feature_counts.csv"
    features.write_text(
        "feature_id,S1,S2,S3\n"
        "gene_a,10,20,30\n"
        "gene_b,2,4,6\n"
    )
    metadata = tmp_path / "metadata.csv"
    metadata.write_text(
        "sample_id,condition,age\n"
        "S1,Control,40\n"
        "S2,Treatment,41\n"
        "S3,Control,39\n"
    )

    contract = build_input_contract([
        _record("features", "feature_table", str(features)),
        _record("metadata", "metadata", str(metadata)),
    ])

    assert contract["status"] == "ready"
    assert contract["required"]["sample_key"] is True
    assert contract["tables"][0]["orientation"] == "features_by_samples"
    assert contract["joins"][0]["status"] == "valid"
    assert contract["grouping_candidates"][0]["column"] == "condition"


def test_contract_blocks_duplicate_or_negative_feature_inputs(tmp_path):
    features = tmp_path / "feature_counts.csv"
    features.write_text(
        "feature_id,S1,S2\n"
        "gene_a,10,-2\n"
        "gene_a,1,3\n"
    )

    contract = build_input_contract([_record("features", "feature_table", str(features))])
    codes = {item["code"] for item in contract["validations"]}

    assert contract["status"] == "invalid"
    assert "duplicate_identifier" in codes
    assert "negative_abundance" in codes
    assert contract["required"]["feature_key"] is True

