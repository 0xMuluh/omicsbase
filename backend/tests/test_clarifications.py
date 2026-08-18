"""Guided elicitation: planner clarification requests, answers, and API endpoints."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.project import Project, UploadedFile
from app.schemas.schemas import (
    AnalysisPlan,
    ClarificationAnswer,
    ClarificationRequest,
)

SQLALCHEMY_DATABASE_URL = "sqlite://"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

MANIFEST_WITH_GROUPS = {
    "status": "ready",
    "domain": "microbiome",
    "files": [
        {"name": "feature_table.tsv", "role": "feature_table", "format": "tsv"},
        {"name": "metadata.tsv", "role": "metadata", "format": "tsv"},
    ],
    "grouping_candidates": [
        {"column": "treatment", "levels": ["control", "disease"], "file": "metadata.tsv"},
        {"column": "sex", "levels": ["M", "F"], "file": "metadata.tsv"},
    ],
}

MANIFEST_NO_GROUPS = {
    "status": "ready",
    "domain": "microbiome",
    "files": [
        {"name": "feature_table.tsv", "role": "feature_table", "format": "tsv"},
        {"name": "metadata.tsv", "role": "metadata", "format": "tsv"},
    ],
    "grouping_candidates": [],
}

MANIFEST_UNCLASSIFIED_CANDIDATE = {
    "status": "ready",
    "domain": "microbiome",
    "files": [
        {"name": "feature_table.tsv", "role": "feature_table", "format": "tsv"},
        {"name": "metadata.tsv", "role": "metadata", "format": "tsv"},
    ],
    "grouping_candidates": [
        {"column": "treatment", "levels": [], "file": "metadata.tsv"},
    ],
}


def _add_input_rows(db, project_id):
    db.add_all([
        UploadedFile(
            project_id=str(project_id),
            file_role="feature_table",
            original_name="features.csv",
            detected_format="csv",
            file_summary={"format": "csv", "columns": ["feature_id", "S1", "S2"]},
        ),
        UploadedFile(
            project_id=str(project_id),
            file_role="metadata",
            original_name="metadata.csv",
            detected_format="csv",
            file_summary={
                "format": "csv",
                "columns": ["sample_id", "condition"],
                "categorical_summary": {"condition": ["control", "treatment"]},
            },
        ),
    ])
    db.commit()


def test_stage_uploaded_files_makes_inputs_visible_before_planning(tmp_path):
    from app.tasks.analysis import _stage_uploaded_files

    upload = tmp_path / "upload.xlsx"
    upload.write_bytes(b"workbook-bytes")
    project_dir = tmp_path / "project"
    record = UploadedFile(
        original_name="metadata.xlsx",
        file_path=str(upload),
    )

    staged = _stage_uploaded_files(project_dir, [record])

    assert staged == ["data/metadata.xlsx"]
    assert (project_dir / "data" / "metadata.xlsx").read_bytes() == b"workbook-bytes"
    assert _stage_uploaded_files(project_dir, [record]) == staged
