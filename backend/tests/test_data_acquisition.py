"""Tests for controlled study data acquisition."""

from __future__ import annotations

import io
import socket
import urllib.error
import urllib.request
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


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/secrets",
        "http://10.0.0.4/data.csv",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.0.2.1/reserved",
        "http://[::1]/secrets",
        "http://[fd00:ec2::254]/metadata",
        "http://metadata.google.internal/computeMetadata/v1/",
    ],
)
def test_fetch_url_rejects_non_public_literal_and_metadata_hosts(tmp_path, monkeypatch, url):
    monkeypatch.setattr(data_acquisition.settings, "projects_dir", str(tmp_path))
    project = SimpleNamespace(id="p1", study_manifest=None, files=[])

    result = data_acquisition.fetch_url_into_study(MagicMock(), project, url=url)

    assert result["status"] == "error"
    assert "public" in result["error"].lower()
    assert not (tmp_path / "uploads").exists()


def test_fetch_url_rejects_hostname_resolving_to_private_address(tmp_path, monkeypatch):
    monkeypatch.setattr(data_acquisition.settings, "projects_dir", str(tmp_path))
    monkeypatch.setattr(
        data_acquisition.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.20.30.40", 443))
        ],
    )
    project = SimpleNamespace(id="p1", study_manifest=None, files=[])

    result = data_acquisition.fetch_url_into_study(
        MagicMock(), project, url="https://files.example/data.csv"
    )

    assert result["status"] == "error"
    assert "non-public" in result["error"].lower()


def test_public_url_validation_rejects_mixed_dns_answers(monkeypatch):
    monkeypatch.setattr(
        data_acquisition.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443)),
        ],
    )

    with pytest.raises(ValueError, match="non-public"):
        data_acquisition._validate_public_http_url("https://mixed.example/data.csv")


def test_public_url_validation_accepts_public_dns_answer(monkeypatch):
    monkeypatch.setattr(
        data_acquisition.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443))
        ],
    )

    data_acquisition._validate_public_http_url("https://files.example/data.csv")


def test_redirect_handler_revalidates_and_blocks_metadata_target():
    handler = data_acquisition._PublicOnlyRedirectHandler()
    request = urllib.request.Request("https://example.com/data.csv")

    with pytest.raises(urllib.error.URLError, match="Unsafe redirect blocked"):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "http://169.254.169.254/latest/meta-data/",
        )


def test_fetch_url_installs_redirect_validator(tmp_path, monkeypatch):
    monkeypatch.setattr(data_acquisition.settings, "projects_dir", str(tmp_path))
    monkeypatch.setattr(
        data_acquisition.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443))
        ],
    )
    captured = {}

    class FakeResponse(io.BytesIO):
        headers = {}

    class FakeOpener:
        def open(self, request, timeout):
            captured["request_url"] = request.full_url
            captured["timeout"] = timeout
            return FakeResponse(b"column\nvalue\n")

    def fake_build_opener(*handlers):
        captured["handlers"] = handlers
        return FakeOpener()

    monkeypatch.setattr(data_acquisition.urllib.request, "build_opener", fake_build_opener)
    monkeypatch.setattr(
        data_acquisition,
        "_register_uploaded_file",
        lambda db, project, path, file_role: {
            "status": "ok",
            "name": path.name,
            "role": file_role,
        },
    )
    monkeypatch.setattr(
        data_acquisition,
        "_refresh_manifest",
        lambda db, project: {
            "status": "ready",
            "domain": "unknown",
            "summary": "test",
            "validations": [],
        },
    )
    project = SimpleNamespace(id="p1", study_manifest=None, files=[])

    result = data_acquisition.fetch_url_into_study(
        MagicMock(), project, url="https://files.example/data.csv"
    )

    assert result["status"] == "ok"
    assert captured["request_url"] == "https://files.example/data.csv"
    assert captured["timeout"] == data_acquisition.DOWNLOAD_TIMEOUT_S
    assert any(
        isinstance(handler, data_acquisition._PublicOnlyRedirectHandler)
        for handler in captured["handlers"]
    )


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
    assert {item["role"] for item in result["files"]} == {"other"}
    assert (tmp_path / "uploads" / "proj-1").exists()
