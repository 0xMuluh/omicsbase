"""Route each user's browser to their own Reports session: a job on Roihu, or a machine of
their own that they attached with a one-time pairing code (a route with no expiry). In a
local install the router also dials out to a user's machine (a "linked" route): the machine
prints a pairing string, and the router keeps a tunnel client running to it.

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
PAIRINGS = STATE / "pairings.json"
AUTHFILE = STATE / "users.json"
SECRET = STATE / "router-secret"
ADMIN_SECRET = STATE / "admin-secret"
PORTS = range(18100, 18200)
LISTEN = (os.environ.get("ROIHU_ROUTER_HOST", "127.0.0.1"), 18095)
# OmicsBase backend, from its Docker network only (librechat_default gateway).
ADMIN_LISTEN = (os.environ.get("ROIHU_ROUTER_ADMIN_HOST", "172.18.0.1"), 18096)
COOKIE = "roihu_route"
PUBLIC_URL = os.environ.get("ROIHU_PUBLIC_URL", "https://openhands.omicsbase.utu.fi")
APP_URL = os.environ.get("ROIHU_APP_URL", "https://omicsbase.utu.fi")
# Local installs: a request without a valid route cookie goes on to the local Reports
# (the check answers 204 without a port) instead of being refused.
CHECK_FALLBACK = os.environ.get("ROIHU_ROUTER_CHECK_FALLBACK") == "1"
# Pairing strings printed by `omicsbase-reports serve` on a user's machine.
MACHINE_PREFIX = "omicsbase-machine:"
OPEN_CODE_SECONDS = 600
PAIRING_SECONDS = 600
# Browser access to an attached machine, whose route itself does not expire.
ATTACHED_COOKIE_SECONDS = 12 * 3600
# Pairing codes: no easily confused characters; 10 of 31 symbols is about 50 bits.
PAIRING_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
PAIRING_LENGTH = 10
# The page runs in auth-required mode (the key stays out of the HTML, since other users on
# the compute node can reach the job's port), so the browser needs a registered backend
# carrying some key. It gets a placeholder; Caddy replaces it with the job's real key.
# Opened in a hidden frame by the OmicsBase workspace, the page reports back instead of
# loading the agent.
OPEN_PAGE = """<!doctype html><meta charset="utf-8"><title>Opening Reports</title>
<script>
const id = "omicsbase-roihu";
let list = [];
try { list = JSON.parse(localStorage.getItem("openhands-backends") || "[]").filter(b => b.id !== id); } catch (e) {}
list.unshift({id, name: "Reports", host: location.origin, apiKey: "set-by-omicsbase", kind: "local"});
localStorage.setItem("openhands-backends", JSON.stringify(list));
const sel = JSON.stringify({backendId: id});
localStorage.setItem("openhands-active-backend", sel);
sessionStorage.setItem("openhands-active-backend", sel);
if (window.parent !== window) {
  window.parent.postMessage({type: "omicsbase:roihu-open"}, "%s");
} else {
  location.replace("/");
}
</script>""" % APP_URL
# Undoes the open page after a disconnect: in a local install the user's machine and the
# Reports on this machine share one address, so the page must stop using the session's backend.
CLOSE_PAGE = """<!doctype html><meta charset="utf-8"><title>Closing Reports</title>
<script>
const id = "omicsbase-roihu";
try {
  const list = JSON.parse(localStorage.getItem("openhands-backends") || "[]").filter(b => b.id !== id);
  localStorage.setItem("openhands-backends", JSON.stringify(list));
} catch (e) {}
for (const store of [localStorage, sessionStorage]) {
  try {
    if ((JSON.parse(store.getItem("openhands-active-backend") || "null") || {}).backendId === id) {
      store.removeItem("openhands-active-backend");
    }
  } catch (e) {}
}
if (window.parent !== window) {
  window.parent.postMessage({type: "omicsbase:roihu-closed"}, "%s");
} else {
  location.replace("%s/");
}
</script>""" % (APP_URL, APP_URL)
LOCK = threading.Lock()


def _load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def _save(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.chmod(0o600)
    tmp.replace(path)


def _admin_secret():
    """Shared with OmicsBase's backend: from the environment in a local install, else a file."""
    return os.environ.get("ROIHU_ROUTER_ADMIN_SECRET") or ADMIN_SECRET.read_text().strip()


def _secret():
    if not SECRET.exists():
        SECRET.write_text(secrets.token_hex(32))
        SECRET.chmod(0o600)
    return SECRET.read_text().strip().encode()


def _live(r):
    """A route is live until it expires; an attached machine's route never does."""
    return r["expires"] is None or r["expires"] >= time.time()


def sign(job, expires, generation=0):
    claims = {"j": job, "e": expires, "g": generation}
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
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
    if not route or claims.get("e", 0) < time.time() or not _live(route):
        return None
    # Signing out raises the route's cookie generation, which ends every older cookie.
    if claims.get("g", 0) != route.get("cookie_generation", 0):
        return None
    return route


def _sync_authfile(routes):
    """Relay credentials: keep entries not made here, replace ours with the live routes."""
    users = {k: v for k, v in _load(AUTHFILE, {}).items() if not k.startswith("job-")}
    for job, r in routes.items():
        users[f"job-{job}:{r['relay_password']}"] = [f"^R:127\\.0\\.0\\.1:{r['port']}$"]
    _save(AUTHFILE, users)


def create_route(user, hours, meta=None, session_key=None, link=None):
    """New route for one job of `user`: tunnel port, relay credential, session key.

    `hours` None makes a route that does not expire (an attached machine). `meta` is kept
    with the route for OmicsBase (job ids, settings, encrypted token, machine name). A linked
    machine brings its own session key and the `link` the router dials out with."""
    with LOCK:
        routes = _prune(_load(ROUTES, {}))
        # A port can still be bound by a tunnel whose route is gone: chisel keeps an open
        # connection after its credential is removed (a disconnected machine left running).
        # Such a port is never handed out again, so that tunnel cannot receive another
        # session's traffic.
        used = {r["port"] for r in routes.values()}
        port = next((p for p in PORTS if p not in used and not _listening(p)), None)
        if port is None:
            raise RuntimeError("no free tunnel port")
        job = secrets.token_hex(8)
        routes[job] = {
            "user": str(user),
            "port": port,
            "expires": None if hours is None else int(time.time() + float(hours) * 3600),
            "session_key": session_key or secrets.token_hex(24),
            "relay_password": secrets.token_hex(24),
            "meta": meta or {},
        }
        if link:
            routes[job]["link"] = link
        _save(ROUTES, routes)
        _sync_authfile(routes)
        r = routes[job]
    if link:
        _start_link(job, r)
    return {
        "route": job,
        "expires": r["expires"],
        "relay_remote": f"R:127.0.0.1:{port}",
        "relay_auth": f"job-{job}:{r['relay_password']}",
        "session_key": r["session_key"],
    }


def _owned(routes, job, user):
    r = routes.get(job)
    if r is None or not _live(r) or (user is not None and r["user"] != str(user)):
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


def set_expires(job, user, expires):
    """A job's route made before it queued lasts, once the job starts, as long as the job."""
    with LOCK:
        routes = _load(ROUTES, {})
        r = _owned(routes, job, user)
        if r is None:
            return False
        r["expires"] = int(expires)
        _save(ROUTES, routes)
        return True


def user_routes(user):
    """The user's live routes, newest first, with meta and tunnel state."""
    routes = _load(ROUTES, {})
    mine = [(j, r) for j, r in routes.items() if r["user"] == str(user) and _live(r)]
    mine.sort(key=lambda jr: jr[1]["expires"] or float("inf"), reverse=True)
    return [{"route": j, "expires": r["expires"], "meta": r.get("meta", {}), "connected": connected(j)} for j, r in mine]


def _listening(port):
    """Whether a tunnel is bound to this port on the VM."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def _answers(port):
    """Whether the machine behind a linked route answers. Its tunnel client keeps listening
    here while the machine is down, so a listening port alone proves nothing."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as conn:
            conn.settimeout(3)
            conn.sendall(b"HEAD / HTTP/1.0\r\n\r\n")
            return conn.recv(1).startswith(b"H")
    except OSError:
        return False


def connected(job, user=None):
    """Whether the job's tunnel is up (its port listens on the VM; for a linked machine, the
    machine answers through it); None if no such route."""
    r = _owned(_load(ROUTES, {}), job, user)
    if r is None:
        return None
    return _answers(r["port"]) if r.get("link") else _listening(r["port"])


def _prune(routes):
    return {j: r for j, r in routes.items() if _live(r)}


def use_open_code(code):
    """Swap a one-time open code for the job's cookie token."""
    with LOCK:
        routes = _load(ROUTES, {})
        for job, r in routes.items():
            if r.get("open_code") and hmac.compare_digest(r["open_code"], code):
                r.pop("open_code")
                valid = r.pop("open_code_expires") >= time.time() and _live(r)
                _save(ROUTES, routes)
                expires = r["expires"] or int(time.time() + ATTACHED_COOKIE_SECONDS)
                return sign(job, expires, r.get("cookie_generation", 0)) if valid else None
    return None


def sign_out(user):
    """End every browser's access to the user's jobs (OmicsBase logout). The jobs keep
    running; the user reconnects with a new open link."""
    with LOCK:
        routes = _load(ROUTES, {})
        for r in routes.values():
            if r["user"] == str(user):
                r["cookie_generation"] = r.get("cookie_generation", 0) + 1
                r.pop("open_code", None)
                r.pop("open_code_expires", None)
        _save(ROUTES, routes)


def _pairing_key(code):
    """Codes are stored hashed and compared without dashes, spaces or case."""
    normal = "".join(c for c in str(code).upper() if c in PAIRING_ALPHABET)
    return hashlib.sha256(normal.encode()).hexdigest() if len(normal) == PAIRING_LENGTH else None


def create_pairing(user):
    """A one-time code with which `user` attaches a machine of their own. A newer code
    replaces the user's earlier one."""
    code = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(PAIRING_LENGTH))
    expires = int(time.time() + PAIRING_SECONDS)
    with LOCK:
        pairings = {k: p for k, p in _load(PAIRINGS, {}).items()
                    if p["expires"] >= time.time() and p["user"] != str(user)}
        pairings[_pairing_key(code)] = {"user": str(user), "expires": expires}
        _save(PAIRINGS, pairings)
    return {"code": f"{code[:5]}-{code[5:]}", "expires": expires}


def claim_pairing(code, name):
    """Use a pairing code: a route with no expiry for the code's user, named after the
    machine. None for an unknown or expired code; "busy" when the user already has a session."""
    key = _pairing_key(code)
    with LOCK:
        pairings = _load(PAIRINGS, {})
        pairing = pairings.pop(key, None) if key else None
        _save(PAIRINGS, {k: p for k, p in pairings.items() if p["expires"] >= time.time()})
    if pairing is None or pairing["expires"] < time.time():
        return None
    if user_routes(pairing["user"]):
        return "busy"
    clean = "".join(c for c in str(name or "") if c.isprintable()).strip()[:64]
    route = create_route(pairing["user"], None, {"kind": "attached", "name": clean or "Your computer"})
    return {**route, "user": pairing["user"]}


# Tunnel clients to linked machines, by route.
LINKS = {}


def parse_pairing(text):
    """The machine's pairing string, or None when it is not one."""
    text = str(text or "").strip()
    if not text.startswith(MACHINE_PREFIX):
        return None
    try:
        body = text[len(MACHINE_PREFIX):]
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        link = {k: data[k] for k in ("address", "port", "fingerprint", "auth", "sessionKey", "target")}
    except (ValueError, KeyError, TypeError):
        return None
    if not (isinstance(link["port"], int) and isinstance(link["target"], int)
            and all(isinstance(link[k], str) and link[k] for k in ("address", "fingerprint", "auth", "sessionKey"))
            and all(c.isalnum() or c in ".-:[]" for c in link["address"])):
        return None
    return link


def _start_link(job, r):
    """Dial out to a linked machine: its agent appears on this route's port."""
    import subprocess
    link = r["link"]
    host = link["address"] if ":" not in link["address"] or link["address"].startswith("[") else f"[{link['address']}]"
    LINKS[job] = subprocess.Popen(
        ["chisel", "client", "--fingerprint", link["fingerprint"], f"http://{host}:{link['port']}",
         f"0.0.0.0:{r['port']}:127.0.0.1:{link['target']}"],
        env={"PATH": os.environ.get("PATH", ""), "AUTH": link["auth"]},
    )


def _stop_link(job):
    client = LINKS.pop(job, None)
    if client is not None:
        client.terminate()


def link_machine(user, pairing):
    """A route to the user's machine from its pairing string. None for a string that is not
    one; "busy" when the user already has a session."""
    link = parse_pairing(pairing)
    if link is None:
        return None
    if user_routes(user):
        return "busy"
    route = create_route(user, None, {"kind": "linked", "name": link["address"]},
                         session_key=link.pop("sessionKey"), link=link)
    return {"route": route["route"], "expires": None}


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
            _stop_link(job)
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
                self.send_response(204 if CHECK_FALLBACK else 401)
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
            body = CLOSE_PAGE.encode()
            self.send_response(200)
            self.send_header("Set-Cookie", f"{COOKIE}=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Lax")
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


class AdminHandler(BaseHTTPRequestHandler):
    """Private interface for OmicsBase's backend, on the Docker network only.

    POST   /routes              {"user", "hours", "meta"} -> route, relay settings, session key
    POST   /pairings            {"user"}          -> {"code", "expires"}: one-time pairing code
    POST   /pairings/claim      {"code", "name"}  -> route, relay settings, session key, user
    POST   /links               {"user", "pairing"} -> route: dial out to the user's machine
    GET    /routes?user=                          -> {"routes": [...]}: live routes with meta
    PUT    /routes/<id>/meta    {"user", "meta"}
    PUT    /routes/<id>/expires {"user", "expires"} -> new expiry (Unix time, within 36 h 15 min)
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
        if given and hmac.compare_digest(given, _admin_secret()):
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
        if parts == ["pairings"]:
            if not body.get("user"):
                return self._reply(400, {"error": "user required"})
            return self._reply(201, create_pairing(body["user"]))
        if parts == ["links"]:
            if not body.get("user"):
                return self._reply(400, {"error": "user required"})
            try:
                route = link_machine(body["user"], body.get("pairing"))
            except RuntimeError as e:
                return self._reply(503, {"error": str(e)})
            if route == "busy":
                return self._reply(409, {"error": "a Reports session is already running"})
            return self._reply(201, route) if route else self._reply(400, {"error": "not a pairing string"})
        if parts == ["pairings", "claim"]:
            try:
                route = claim_pairing(body.get("code", ""), body.get("name"))
            except RuntimeError as e:
                return self._reply(503, {"error": str(e)})
            if route == "busy":
                return self._reply(409, {"error": "a Reports session is already running"})
            return self._reply(201, route) if route else self._reply(404)
        if len(parts) == 3 and parts[0] == "users" and parts[2] == "sign-out":
            sign_out(parts[1])
            return self._reply(204)
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
        if len(parts) == 3 and parts[0] == "routes" and parts[2] == "expires":
            expires = body.get("expires")
            if not isinstance(expires, (int, float)) or not 0 < expires - time.time() <= 36.25 * 3600:
                return self._reply(400, {"error": "expires (within 36 h 15 min) required"})
            return self._reply(204) if set_expires(parts[1], body.get("user"), expires) else self._reply(404)
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
        for job, r in _load(ROUTES, {}).items():
            if r.get("link") and _live(r):
                _start_link(job, r)
        if not os.environ.get("ROIHU_ROUTER_ADMIN_SECRET") and not ADMIN_SECRET.exists():
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
