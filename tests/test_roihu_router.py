"""Routing cookies for Reports on Roihu (deployment/roihu/roihu_router.py)."""
import importlib.util
from pathlib import Path

import pytest

ROUTER = Path(__file__).resolve().parents[1] / "deployment" / "roihu" / "roihu_router.py"


@pytest.fixture
def router(tmp_path, monkeypatch):
    monkeypatch.setenv("ROIHU_ROUTER_STATE", str(tmp_path))
    spec = importlib.util.spec_from_file_location("roihu_router", ROUTER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cookie_for(router, job, user):
    code = router.open_link(job, user).split("c=")[1]
    return router.use_open_code(code)


def test_open_code_works_once(router):
    job = router.create_route("u1", 1)["route"]
    code = router.open_link(job, "u1").split("c=")[1]
    assert router.verify(router.use_open_code(code))["user"] == "u1"
    assert router.use_open_code(code) is None


def test_sign_out_ends_older_cookies_only_for_that_user(router):
    mine = router.create_route("u1", 1)["route"]
    theirs = router.create_route("u2", 1)["route"]
    my_cookie, their_cookie = cookie_for(router, mine, "u1"), cookie_for(router, theirs, "u2")
    router.sign_out("u1")
    assert router.verify(my_cookie) is None
    assert router.verify(their_cookie) is not None
    assert router.verify(cookie_for(router, mine, "u1"))["user"] == "u1"


def test_cookies_from_before_generations_still_work(router):
    job = router.create_route("u1", 1)["route"]
    route = router._load(router.ROUTES, {})[job]
    legacy_body = router.base64.urlsafe_b64encode(
        router.json.dumps({"j": job, "e": route["expires"]}).encode()
    ).decode().rstrip("=")
    mac = router.hmac.new(router._secret(), legacy_body.encode(), router.hashlib.sha256).hexdigest()
    assert router.verify(f"{legacy_body}.{mac}") is not None
