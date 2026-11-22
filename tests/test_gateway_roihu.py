"""The OmicsBase gateway in a user's own Reports job on Roihu (OMICSBASE_GATEWAY_MODE=roihu)."""
import importlib
import sys
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

GATEWAY_PARENT = Path(__file__).resolve().parents[1] / "docker" / "openhands"
REPORT = "0123456789abcdef01234567"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("OMICSBASE_GATEWAY_MODE", "roihu")
    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("LOCAL_BACKEND_API_KEY", "job-key")
    monkeypatch.delenv("OMICSBASE_AUTH_SECRET", raising=False)
    monkeypatch.syspath_prepend(str(GATEWAY_PARENT))
    for name in [m for m in sys.modules if m == "gateway" or m.startswith("gateway.")]:
        del sys.modules[name]
    app = importlib.import_module("gateway.app").app
    return TestClient(app)


KEY = {"X-Session-API-Key": "job-key"}


def test_requests_without_the_session_key_are_refused(client):
    assert client.post(f"/api/omicsbase/projects/{REPORT}/render").status_code == 401
    assert client.get("/api/omicsbase/editor/default/tree", headers={"X-Session-API-Key": "other"}).status_code == 401
    assert client.get("/api/omicsbase/health").status_code == 200


def test_session_reads_the_report_from_an_unsigned_ticket(client):
    ticket = jwt.encode({"sub": "u1", "project_id": REPORT}, "any-secret", algorithm="HS256")
    res = client.post("/api/omicsbase/session", json={"ticket": ticket}, headers=KEY)
    assert res.status_code == 200
    assert res.json()["project_id"] == REPORT


def test_expired_ticket_is_refused(client):
    ticket = jwt.encode({"sub": "u1", "project_id": REPORT, "exp": 1}, "any-secret", algorithm="HS256")
    assert client.post("/api/omicsbase/session", json={"ticket": ticket}, headers=KEY).status_code == 403


def test_report_status_comes_from_the_report_folder(client, tmp_path):
    (tmp_path / REPORT / "_site").mkdir(parents=True)
    (tmp_path / REPORT / "_site" / "index.html").write_text("<html>")
    res = client.get(f"/api/omicsbase/report/{REPORT}/status", headers=KEY)
    assert res.status_code == 200
    assert res.json()["has_site"] is True


def test_report_ids_are_checked(client):
    assert client.post("/api/omicsbase/projects/..%2Fetc/render", headers=KEY).status_code in (400, 404)
    assert client.post("/api/omicsbase/projects/notanid/render", headers=KEY).status_code == 400


def test_editor_opens_the_most_recent_report(client, tmp_path):
    (tmp_path / REPORT).mkdir()
    (tmp_path / REPORT / "analysis.R").write_text("1")
    tree = client.get("/api/omicsbase/editor/default/tree", headers=KEY)
    assert tree.status_code == 200
    assert "analysis.R" in tree.text


def test_agent_hears_of_csc_modules_only_when_the_job_binds_them(client, monkeypatch):
    from gateway.sessions import build_omicsbase_system_prompt as prompt

    monkeypatch.delenv("OMICSBASE_CSC_MODULES", raising=False)
    assert "module load" not in prompt(REPORT, "/projects", "openhands")
    assert "No command-line tools" in prompt(REPORT, "/projects", "openhands")
    monkeypatch.setenv("OMICSBASE_CSC_MODULES", "1")
    assert "module load bio-apps/v202603" in prompt(REPORT, "/projects", "openhands")
    assert "/dataset/project_2020345" in prompt(REPORT, "/projects", "openhands")
