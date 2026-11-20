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


def test_a_pairing_code_attaches_one_machine_once(router):
    code = router.create_pairing("u1")["code"]
    route = router.claim_pairing(code.lower().replace("-", " "), "lab-pc\x07")
    assert route["user"] == "u1" and route["expires"] is None
    assert route["relay_auth"].startswith(f"job-{route['route']}:")
    assert router.claim_pairing(code, "again") is None
    [live] = router.user_routes("u1")
    assert live["meta"] == {"kind": "attached", "name": "lab-pc"}


def test_an_attached_machine_stays_until_removed_and_its_cookies_expire(router, monkeypatch):
    route = router.claim_pairing(router.create_pairing("u1")["code"], "pc")["route"]
    cookie = cookie_for(router, route, "u1")
    later = router.time.time() + router.ATTACHED_COOKIE_SECONDS + 60
    monkeypatch.setattr(router.time, "time", lambda: later)
    assert router.verify(cookie) is None
    assert router._prune(router._load(router.ROUTES, {}))
    assert router.verify(cookie_for(router, route, "u1"))["user"] == "u1"
    assert router.remove(route, "u1") and router.user_routes("u1") == []


def test_pairing_codes_expire_and_a_newer_code_replaces_the_older(router, monkeypatch):
    first = router.create_pairing("u1")["code"]
    second = router.create_pairing("u1")["code"]
    assert router.claim_pairing(first, "pc") is None
    now = router.time.time()
    monkeypatch.setattr(router.time, "time", lambda: now + router.PAIRING_SECONDS + 1)
    assert router.claim_pairing(second, "pc") is None


def test_a_user_with_a_session_cannot_attach_another_machine(router):
    router.create_route("u1", 1)
    assert router.claim_pairing(router.create_pairing("u1")["code"], "pc") == "busy"
    assert router.claim_pairing("not-a-code", "pc") is None


def test_a_port_still_bound_by_an_old_tunnel_is_not_handed_out(router, monkeypatch):
    import socket
    with socket.socket() as squatter:
        squatter.bind(("127.0.0.1", 0))
        squatter.listen()
        held = squatter.getsockname()[1]
        monkeypatch.setattr(router, "PORTS", range(held, held + 2))
        route = router.create_route("u1", 1)["route"]
        assert router._load(router.ROUTES, {})[route]["port"] == held + 1


def pairing_string(router, **changes):
    data = {"address": "lab.example.org", "port": 8443, "fingerprint": "fp", "auth": "machine:pw",
            "sessionKey": "machine-key", "target": 3000, **changes}
    body = router.base64.urlsafe_b64encode(router.json.dumps(data).encode()).decode().rstrip("=")
    return router.MACHINE_PREFIX + body


def test_a_pairing_string_links_the_users_machine_with_its_own_key(router, monkeypatch):
    started, stopped = [], []
    monkeypatch.setattr(router, "_start_link", lambda job, r: started.append((job, r["link"])))
    monkeypatch.setattr(router, "_stop_link", lambda job: stopped.append(job))
    route = router.link_machine("u1", " " + pairing_string(router) + "\n")["route"]
    saved = router._load(router.ROUTES, {})[route]
    assert saved["session_key"] == "machine-key" and saved["expires"] is None
    assert started == [(route, {"address": "lab.example.org", "port": 8443, "fingerprint": "fp",
                                "auth": "machine:pw", "target": 3000})]
    [live] = router.user_routes("u1")
    assert live["meta"] == {"kind": "linked", "name": "lab.example.org"} and "link" not in live
    assert router.link_machine("u1", pairing_string(router)) == "busy"
    assert router.remove(route, "u1") and stopped == [route]


def test_only_well_formed_pairing_strings_are_accepted(router):
    assert router.parse_pairing("not a pairing string") is None
    assert router.parse_pairing(router.MACHINE_PREFIX + "%%%") is None
    assert router.parse_pairing(pairing_string(router, port="8443")) is None
    assert router.parse_pairing(pairing_string(router, address="evil.org/x; rm")) is None
    assert router.parse_pairing(pairing_string(router, address="[2001:db8::1]"))["address"] == "[2001:db8::1]"


def test_local_check_falls_through_to_local_reports_without_a_route(router, monkeypatch):
    import http.client
    import threading
    from http.server import ThreadingHTTPServer
    for fallback, expected in ((False, 401), (True, 204)):
        monkeypatch.setattr(router, "CHECK_FALLBACK", fallback)
        server = ThreadingHTTPServer(("127.0.0.1", 0), router.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
        conn.request("GET", "/check", headers={"Cookie": "roihu_route=forged.token"})
        assert conn.getresponse().status == expected
        server.shutdown()
