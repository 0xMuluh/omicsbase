"""Route each user's browser to their own Reports job on Roihu.

Every job gets its own tunnel port on the VM and its own relay credential that may open
only that port (chisel reloads its authfile on change). The open link carries a one-time
code (valid once, for 10 minutes) that is swapped for a cookie with a signed token naming
the job; Caddy asks `/check` on every request, and only a valid,
unexpired token yields the job's port and session key, which Caddy adds to the request.
The cookie holds neither the port nor the key.

    roihu_router.py serve          # check service for Caddy (127.0.0.1:18095) and the
                                   # private interface for OmicsBase (Docker network, :18096)
    roihu_router.py add <user> <hours>   # new route; prints its relay settings and key
    roihu_router.py link <route>   # fresh one-time open link
    roihu_router.py remove <route>
    roihu_router.py prune          # drop expired routes
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import socket
import sys
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

STATE = Path(os.environ.get("ROIHU_ROUTER_STATE", "/srv/omicsbase/roihu-relay"))
ROUTES = STATE / "routes.json"
AUTHFILE = STATE / "users.json"
SECRET = STATE / "router-secret"
ADMIN_SECRET = STATE / "admin-secret"
PORTS = range(18100, 18200)
LISTEN = ("127.0.0.1", 18095)
# OmicsBase backend, from its Docker network only (librechat_default gateway).
ADMIN_LISTEN = (os.environ.get("ROIHU_ROUTER_ADMIN_HOST", "172.18.0.1"), 18096)
COOKIE = "roihu_route"
PUBLIC_URL = "https://openhands.omicsbase.utu.fi"
OPEN_CODE_SECONDS = 600
# The page runs in auth-required mode (the key stays out of the HTML, since other users on
# the compute node can reach the job's port), so the browser needs a registered backend
# carrying some key. It gets a placeholder; Caddy replaces it with the job's real key.
OPEN_PAGE = """<!doctype html><meta charset="utf-8"><title>Opening Reports</title>
<script>
const id = "omicsbase-roihu";
let list = [];
try { list = JSON.parse(localStorage.getItem("openhands-backends") || "[]").filter(b => b.id !== id); } catch (e) {}
list.unshift({id, name: "Roihu", host: location.origin, apiKey: "set-by-omicsbase", kind: "local"});
localStorage.setItem("openhands-backends", JSON.stringify(list));
const sel = JSON.stringify({backendId: id});
localStorage.setItem("openhands-active-backend", sel);
sessionStorage.setItem("openhands-active-backend", sel);
location.replace("/");
</script>"""
LOCK = threading.Lock()


def _load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def _save(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.chmod(0o600)
    tmp.replace(path)


def _secret():
    if not SECRET.exists():
        SECRET.write_text(secrets.token_hex(32))
        SECRET.chmod(0o600)
    return SECRET.read_text().strip().encode()


def sign(job, expires):
    body = base64.urlsafe_b64encode(json.dumps({"j": job, "e": expires}).encode()).decode().rstrip("=")
    mac = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{mac}"


def verify(token):
    """Return the route for a valid, unexpired token, else None."""
    try:
        body, mac = token.split(".")
        good = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(mac, good):
            return None
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except Exception:
        return None
    route = _load(ROUTES, {}).get(claims.get("j"))
    if not route or claims.get("e", 0) < time.time() or route["expires"] < time.time():
        return None
    return route


def _sync_authfile(routes):
    """Relay credentials: keep entries not made here, replace ours with the live routes."""
    users = {k: v for k, v in _load(AUTHFILE, {}).items() if not k.startswith("job-")}
    for job, r in routes.items():
        users[f"job-{job}:{r['relay_password']}"] = [f"^R:127\\.0\\.0\\.1:{r['port']}$"]
    _save(AUTHFILE, users)


def create_route(user, hours, meta=None):
    """New route for one job of `user`: tunnel port, relay credential, session key.

    `meta` is kept with the route for OmicsBase (job ids, settings, encrypted token)."""
    with LOCK:
        routes = _prune(_load(ROUTES, {}))
        used = {r["port"] for r in routes.values()}
        port = next((p for p in PORTS if p not in used), None)
        if port is None:
            raise RuntimeError("no free tunnel port")
        job = secrets.token_hex(8)
        routes[job] = {
            "user": str(user),
            "port": port,
            "expires": int(time.time() + float(hours) * 3600),
            "session_key": secrets.token_hex(24),
            "relay_password": secrets.token_hex(24),
            "meta": meta or {},
        }
        _save(ROUTES, routes)
        _sync_authfile(routes)
        r = routes[job]
    return {
        "route": job,
        "expires": r["expires"],
        "relay_remote": f"R:127.0.0.1:{port}",
        "relay_auth": f"job-{job}:{r['relay_password']}",
        "session_key": r["session_key"],
    }


def _owned(routes, job, user):
    r = routes.get(job)
    if r is None or r["expires"] < time.time() or (user is not None and r["user"] != str(user)):
        return None
    return r


def open_link(job, user=None):
    """A fresh one-time open link for a live route, or None."""
    with LOCK:
        routes = _load(ROUTES, {})
        r = _owned(routes, job, user)
        if r is None:
            return None
        r["open_code"] = secrets.token_urlsafe(24)
        r["open_code_expires"] = int(time.time() + OPEN_CODE_SECONDS)
        _save(ROUTES, routes)
    return f"{PUBLIC_URL}/roihu/open?c={r['open_code']}"


def set_meta(job, user, meta):
    with LOCK:
        routes = _load(ROUTES, {})
        r = _owned(routes, job, user)
        if r is None:
            return False
        r["meta"] = meta
        _save(ROUTES, routes)
        return True


def user_routes(user):
    """The user's live routes, newest first, with meta and tunnel state."""
    routes = _load(ROUTES, {})
    mine = [(j, r) for j, r in routes.items() if r["user"] == str(user) and r["expires"] >= time.time()]
    mine.sort(key=lambda jr: jr[1]["expires"], reverse=True)
    return [{"route": j, "expires": r["expires"], "meta": r.get("meta", {}), "connected": connected(j)} for j, r in mine]


def connected(job, user=None):
    """Whether the job's tunnel is up (its port listens on the VM); None if no such route."""
    r = _owned(_load(ROUTES, {}), job, user)
    if r is None:
        return None
    try:
        with socket.create_connection(("127.0.0.1", r["port"]), timeout=1):
            return True
    except OSError:
        return False


def _prune(routes):
    return {j: r for j, r in routes.items() if r["expires"] >= time.time()}


def use_open_code(code):
    """Swap a one-time open code for the job's cookie token."""
    with LOCK:
        routes = _load(ROUTES, {})
        for job, r in routes.items():
            if r.get("open_code") and hmac.compare_digest(r["open_code"], code):
                r.pop("open_code")
                valid = r.pop("open_code_expires") >= time.time() and r["expires"] >= time.time()
                _save(ROUTES, routes)
                return sign(job, r["expires"]) if valid else None
    return None


def _prune_loop():
    while True:
        time.sleep(300)
        with LOCK:
            routes = _load(ROUTES, {})
            live = _prune(routes)
            if live != routes:
                _save(ROUTES, live)
                _sync_authfile(live)


def remove(job, user=None):
    with LOCK:
        routes = _load(ROUTES, {})
        if job in routes and (user is None or routes[job]["user"] == str(user)):
            routes.pop(job)
            _save(ROUTES, routes)
            _sync_authfile(routes)
            return True
    return False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/check":
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            route = verify(cookie[COOKIE].value) if COOKIE in cookie else None
            if route is None:
                self.send_response(401)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("X-Roihu-Port", str(route["port"]))
            self.send_header("X-Session-API-Key", route["session_key"])
            self.end_headers()
        elif url.path == "/roihu/open":
            token = use_open_code(parse_qs(url.query).get("c", [""])[0])
            if token is None:
                self.send_response(403)
                self.end_headers()
                return
            body = OPEN_PAGE.encode()
            self.send_response(200)
            self.send_header("Set-Cookie", f"{COOKIE}={token}; Path=/; Secure; HttpOnly; SameSite=Lax")
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif url.path == "/roihu/close":
            self.send_response(302)
            self.send_header("Set-Cookie", f"{COOKIE}=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Lax")
            self.send_header("Location", "https://omicsbase.utu.fi/")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


class AdminHandler(BaseHTTPRequestHandler):
    """Private interface for OmicsBase's backend, on the Docker network only.

    POST   /routes              {"user", "hours", "meta"} -> route, relay settings, session key
    GET    /routes?user=                          -> {"routes": [...]}: live routes with meta
    PUT    /routes/<id>/meta    {"user", "meta"}
    POST   /routes/<id>/link    {"user"}          -> {"url"}: one-time open link
    GET    /routes/<id>?user=                     -> {"connected"}
    DELETE /routes/<id>?user=
    """

    def log_message(self, *args):
        pass

    def _reply(self, status, data=None):
        body = json.dumps(data).encode() if data is not None else b""
        self.send_response(status)
        if data is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorised(self):
        given = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if given and hmac.compare_digest(given, ADMIN_SECRET.read_text().strip()):
            return True
        self._reply(401)
        return False

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return {}

    def _parts(self):
        url = urlsplit(self.path)
        return url.path.strip("/").split("/"), parse_qs(url.query).get("user", [None])[0]

    def do_POST(self):
        if not self._authorised():
            return
        parts, _ = self._parts()
        body = self._body()
        if parts == ["routes"]:
            hours = body.get("hours")
            if not body.get("user") or not isinstance(hours, (int, float)) or not 0 < hours <= 36:
                return self._reply(400, {"error": "user and hours (0-36) required"})
            try:
                return self._reply(201, create_route(body["user"], hours, body.get("meta")))
            except RuntimeError as e:
                return self._reply(503, {"error": str(e)})
        if len(parts) == 3 and parts[0] == "routes" and parts[2] == "link":
            url = open_link(parts[1], body.get("user"))
            return self._reply(200, {"url": url}) if url else self._reply(404)
        self._reply(404)

    def do_PUT(self):
        if not self._authorised():
            return
        parts, _ = self._parts()
        body = self._body()
        if len(parts) == 3 and parts[0] == "routes" and parts[2] == "meta" and isinstance(body.get("meta"), dict):
            return self._reply(204) if set_meta(parts[1], body.get("user"), body["meta"]) else self._reply(404)
        self._reply(404)

    def do_GET(self):
        if not self._authorised():
            return
        parts, user = self._parts()
        if parts == ["routes"] and user:
            return self._reply(200, {"routes": user_routes(user)})
        if len(parts) == 2 and parts[0] == "routes" and user:
            up = connected(parts[1], user)
            return self._reply(200, {"connected": up}) if up is not None else self._reply(404)
        self._reply(404)

    def do_DELETE(self):
        if not self._authorised():
            return
        parts, user = self._parts()
        if len(parts) == 2 and parts[0] == "routes" and user:
            return self._reply(204) if remove(parts[1], user) else self._reply(404)
        self._reply(404)


def _serve(address, handler):
    threading.Thread(target=ThreadingHTTPServer(address, handler).serve_forever, daemon=True).start()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "serve":
        if not ADMIN_SECRET.exists():
            ADMIN_SECRET.write_text(secrets.token_hex(32))
            ADMIN_SECRET.chmod(0o600)
        _serve(ADMIN_LISTEN, AdminHandler)
        threading.Thread(target=_prune_loop, daemon=True).start()
        ThreadingHTTPServer(LISTEN, Handler).serve_forever()
    elif cmd == "add" and len(sys.argv) == 4:
        print(json.dumps(create_route(sys.argv[2], float(sys.argv[3])), indent=2))
    elif cmd == "link" and len(sys.argv) == 3:
        print(open_link(sys.argv[2]) or "no such live route")
    elif cmd == "remove" and len(sys.argv) == 3:
        remove(sys.argv[2])
    elif cmd == "prune":
        routes = _prune(_load(ROUTES, {}))
        _save(ROUTES, routes)
        _sync_authfile(routes)
    else:
        sys.exit(__doc__)
