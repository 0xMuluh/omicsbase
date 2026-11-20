"""The OmicsBase gateway when one Reports container serves several users (local installs)."""
import importlib
import json
import sys
import time
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

GATEWAY_PARENT = Path(__file__).resolve().parents[1] / "docker" / "openhands"
SECRET = "a-test-secret-that-is-long-enough-for-hs256"
APP = "http://localhost:3080"
REPORT = "0123456789abcdef01234567"


@pytest.fixture
def gw(tmp_path, monkeypatch):
    monkeypatch.delenv("OMICSBASE_GATEWAY_MODE", raising=False)
    monkeypatch.setenv("OMICSBASE_AUTH_SECRET", SECRET)
    monkeypatch.setenv("DOMAIN_CLIENT", APP)
    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.syspath_prepend(str(GATEWAY_PARENT))
    for name in [m for m in sys.modules if m == "gateway" or m.startswith("gateway.")]:
        del sys.modules[name]
    core = importlib.import_module("gateway.core")
    monkeypatch.setattr(core, "SESSION_REGISTRY_DIR", tmp_path / "sessions")
    app = importlib.import_module("gateway.app").app
    for user in ("alice", "bob"):
        report = tmp_path / "projects" / "users" / user / REPORT
        (report / "_site").mkdir(parents=True)
        (report / "_site" / "index.html").write_text(f"{user}'s report")
        (report / "analysis.R").write_text(f"# {user}")
    return TestClient(app), tmp_path


def cookie(user):
    token = jwt.encode({"sub": user, "project_id": REPORT, "exp": int(time.time()) + 60}, SECRET, algorithm="HS256")
    return {"omicsbase_user_token": token}


def register(tmp_path, conversation_id, user):
    (tmp_path / "sessions").mkdir(exist_ok=True)
    (tmp_path / "sessions" / f"{conversation_id}.json").write_text(json.dumps({"user_id": user}))


def test_report_files_need_a_signed_in_user(gw):
    client, _ = gw
    assert client.get(f"/api/omicsbase/report/{REPORT}/site/index.html").status_code == 401
    assert client.get("/api/omicsbase/editor/default/tree").status_code == 401


def test_the_unsigned_user_id_cookie_is_not_an_identity(gw):
    client, _ = gw
    client.cookies.set("omicsbase_user_id", "alice")
    assert client.get(f"/api/omicsbase/report/{REPORT}/site/index.html").status_code == 401


def test_an_expired_or_forged_user_token_is_refused(gw):
    client, _ = gw
    old = jwt.encode({"sub": "alice", "exp": 1}, SECRET, algorithm="HS256")
    forged = jwt.encode({"sub": "alice"}, "another-secret-that-is-long-enough-too", algorithm="HS256")
    for token in (old, forged):
        res = client.get(f"/api/omicsbase/report/{REPORT}/site/index.html", cookies={"omicsbase_user_token": token})
        assert res.status_code == 401


def test_each_user_sees_only_their_own_report(gw):
    client, _ = gw
    for user in ("alice", "bob"):
        res = client.get(f"/api/omicsbase/report/{REPORT}/site/index.html", cookies=cookie(user))
        assert res.text == f"{user}'s report"
        tree = client.get("/api/omicsbase/editor/default/tree", cookies=cookie(user))
        assert "analysis.R" in tree.text


def test_a_user_without_reports_does_not_fall_back_to_someone_elses(gw):
    client, _ = gw
    res = client.get(f"/api/omicsbase/report/{REPORT}/site/index.html", cookies=cookie("carol"))
    assert res.status_code == 404
    assert client.get("/api/omicsbase/editor/default/tree", cookies=cookie("carol")).json() == []


def test_editor_writes_stay_in_the_users_folder(gw):
    client, tmp_path = gw
    res = client.post(f"/api/omicsbase/editor/{REPORT}/file", params={"path": "../../bob/x.R"},
                      json={"content": "1"}, cookies=cookie("alice"))
    assert res.status_code == 403
    res = client.post(f"/api/omicsbase/editor/{REPORT}/file", params={"path": "notes.R"},
                      json={"content": "1"}, cookies=cookie("alice"))
    assert res.status_code == 200
    assert (tmp_path / "projects" / "users" / "alice" / REPORT / "notes.R").is_file()


def test_a_registered_folder_of_another_user_is_not_followed(gw):
    client, tmp_path = gw
    (tmp_path / "sessions").mkdir()
    bob_dir = tmp_path / "projects" / "users" / "bob" / REPORT
    (tmp_path / "sessions" / "conv-b.json").write_text(json.dumps({"user_id": "bob", "project_dir": str(bob_dir)}))
    tree = client.get("/api/omicsbase/editor/conv-b/file", params={"path": "analysis.R"}, cookies=cookie("alice"))
    assert tree.json()["content"] == "# alice"


def test_conversations_are_limited_to_their_owner(gw):
    client, tmp_path = gw
    register(tmp_path, "conv-a", "alice")
    assert client.get("/api/conversations/conv-a").status_code == 401
    assert client.get("/api/conversations/conv-a", cookies=cookie("bob")).status_code == 403
    assert client.post("/api/conversations/conv-a/events", cookies=cookie("bob")).status_code == 403
    assert client.get("/api/conversations/unregistered", cookies=cookie("alice")).status_code == 403
    assert client.post("/api/conversations", json={}).status_code == 401


def test_reports_render_in_the_callers_own_folder(gw, monkeypatch):
    client, tmp_path = gw
    project = sys.modules["gateway.project"]
    rendered = []

    class Done:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def quarto(*args, cwd=None, **kwargs):
        rendered.append(Path(cwd))
        return Done()
    monkeypatch.setattr(project.asyncio, "create_subprocess_exec", quarto)
    assert client.post(f"/api/omicsbase/projects/{REPORT}/render").status_code == 401
    assert client.post(f"/api/omicsbase/projects/{REPORT}/render", cookies=cookie("alice")).status_code == 200
    assert rendered == [tmp_path / "projects" / "users" / "alice" / REPORT]


def test_cross_origin_requests_only_from_the_app(gw):
    client, _ = gw
    ok = client.options("/api/omicsbase/session", headers={"Origin": APP, "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-origin") == APP
    other = client.options("/api/omicsbase/session",
                           headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in other.headers


def test_session_sets_an_expiring_signed_cookie_and_no_unsigned_one(gw, monkeypatch):
    client, _ = gw
    sessions = sys.modules["gateway.sessions"]

    async def no_llm():
        return None
    monkeypatch.setattr(sessions, "ensure_llm_settings", no_llm)
    ticket = jwt.encode({"sub": "alice", "project_id": REPORT, "aud": "omicsbase-openhands", "iss": "librechat",
                         "exp": int(time.time()) + 60}, SECRET, algorithm="HS256")
    res = client.post("/api/omicsbase/session", json={"ticket": ticket})
    assert res.status_code == 200
    assert "omicsbase_user_id" not in res.cookies
    claims = jwt.decode(res.cookies["omicsbase_user_token"], SECRET, algorithms=["HS256"])
    assert claims["sub"] == "alice" and claims["exp"] > time.time()
