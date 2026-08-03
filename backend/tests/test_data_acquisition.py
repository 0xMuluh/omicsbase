"""Tests for controlled study data acquisition."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services import data_acquisition


def test_list_importable_datasets_includes_globalpatterns():
    datasets = data_acquisition.list_importable_datasets()
    assert any(item["package"] == "phyloseq" and item["dataset"] == "GlobalPatterns" for item in datasets)


def test_fetch_url_rejects_non_http(tmp_path, monkeypatch):
    monkeypatch.setattr(data_acquisition.settings, "projects_dir", str(tmp_path))
    project = SimpleNamespace(id="p1", study_manifest=None, files=[])
    result = data_acquisition.fetch_url_into_study(
        MagicMock(),
        project,
        url="file:///etc/passwd",
    )
    assert result["status"] == "error"
    assert "http" in result["error"].lower()


def test_import_package_dataset_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(data_acquisition.settings, "projects_dir", str(tmp_path))
    project = SimpleNamespace(id="p1", study_manifest=None, files=[])
    result = data_acquisition.import_package_dataset(
        MagicMock(),
        project,
        package="notapkg",
        dataset="nodata",
    )
    assert result["status"] == "error"
    assert "allowlist" in result["error"].lower()


def test_import_package_dataset_registers_files(tmp_path, monkeypatch):
    monkeypatch.setattr(data_acquisition.settings, "projects_dir", str(tmp_path))

    def fake_run_command(cmd, cwd, timeout=1800):
        work_path = Path(cwd)
        for f in work_path.glob("*.csv"):
            f.unlink()
        (work_path / "phyloseq_GlobalPatterns_feature_table.csv").write_text(
            ",S1,S2\nASV1,1,2\nASV2,3,4\n"
        )
        (work_path / "phyloseq_GlobalPatterns_metadata.csv").write_text(
            ",SampleType\nS1,Soil\nS2,Feces\n"
        )
        return True, ""

    monkeypatch.setattr(data_acquisition, "run_command_sync", fake_run_command)

    stored = []

    class FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return None

        def all(self):
            return stored

    class FakeDB:
        def query(self, model):
            return FakeQuery()

        def add(self, obj):
            stored.append(obj)

        def delete(self, obj):
            if obj in stored:
                stored.remove(obj)

        def commit(self):
            return None

        def refresh(self, obj):
            if getattr(obj, "id", None) is None:
                obj.id = "file-1"
            if hasattr(obj, "study_manifest"):
                return None

    project = SimpleNamespace(id="proj-1", study_manifest=None, files=[])
    result = data_acquisition.import_package_dataset(
        FakeDB(),
        project,
        package="phyloseq",
        dataset="GlobalPatterns",
    )
    assert result["status"] == "ok"
    assert len(result["files"]) == 2
    assert (tmp_path / "uploads" / "proj-1").exists()
