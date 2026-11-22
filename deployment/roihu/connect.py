"""Make this machine the Reports computer of an OmicsBase account ("Use my own computer").

For omicsbase.utu.fi, the machine dials out:

    docker run -d --restart unless-stopped --name omicsbase-reports \\
      -v omicsbase-reports:/home/openhands -v omicsbase-projects:/projects \\
      <this image> https://omicsbase.utu.fi --pair ABCDE-FGHJK --name "$(hostname)"

The first start trades the one-time pairing code for this machine's relay credential and
session key, kept in the home volume; later starts (a restart, a reboot) reuse them. Nothing
needs to reach this machine from outside.

For an OmicsBase installed on a laptop, which nothing can dial into, the install dials out to
this machine instead, on one port:

    docker run -d --restart unless-stopped --name omicsbase-reports -p 8443:8443 \\
      -v omicsbase-reports:/home/openhands -v omicsbase-projects:/projects \\
      <this image> serve --address <this machine's name or IP>

It prints a pairing string (`docker logs omicsbase-reports`) to paste into Reports. The tunnel
is encrypted and authenticated by its pinned key; its credential opens only the agent.

Either way the agent starts as in a Roihu job (reports.sbatch), listening on 127.0.0.1 only.
"""
import argparse
import base64
import hashlib
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HOME = Path.home() / ".omicsbase"
STATE = HOME / "attach.json"
ENTRYPOINT = "/opt/agent-canvas/entrypoint.roihu.sh"
MACHINE_PREFIX = "omicsbase-machine:"
# The tunnel server inside the container; publish it with `docker run -p <port>:8443`.
SERVE_PORT = 8443


def fail(message):
    sys.exit(f"OmicsBase Reports: {message}")


def halt(message):
    """Say what is wrong and stay up, for an error that starting again cannot fix. Exiting,
    with any status, would make `--restart unless-stopped` start the container into the same
    error again and again, burying the message in `docker logs`."""
    print(f"OmicsBase Reports: {message}", flush=True)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # `docker stop` ends it at once
    while True:
        time.sleep(3600)


def private_file(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600)
    path.write_text(text)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def claim(app, code, name):
    """Trade the pairing code for this machine's relay settings and session key."""
    request = urllib.request.Request(
        f"{app}/api/roihu/attach/claim",
        data=json.dumps({"code": code, "name": name}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            halt("this pairing code is unknown, used or expired. "
                 "Run docker rm -f omicsbase-reports, then get a new command in Reports.")
        if e.code == 409:
            # The refused claim used the code up.
            halt("you already have a Reports session running. Stop it in Reports, "
                 "run docker rm -f omicsbase-reports, then get a new command there.")
        fail(f"{app} answered {e.code} to the pairing code.")
    except (urllib.error.URLError, OSError) as e:
        fail(f"could not reach {app}: {e}")


def settings(app, code, name):
    """Saved settings for this pairing, or new ones when there are none or the code is new."""
    digest = hashlib.sha256(code.encode()).hexdigest() if code else None
    if STATE.exists():
        saved = json.loads(STATE.read_text())
        if saved.get("app") == app and (digest is None or saved.get("code") == digest):
            return saved
    if not code:
        fail("this machine is not paired yet. Start it with the command shown in Reports.")
    saved = {**claim(app, code, name), "app": app, "code": digest}
    private_file(STATE, json.dumps(saved))
    return saved


def start_agent(session_key, port):
    """Start the agent, its gateway and page server on 127.0.0.1, as in a Roihu job."""
    env = {
        **os.environ,
        "PORT": str(port),
        "AGENT_SERVER_PORT": str(free_port()),
        "AUTOMATION_PORT": str(free_port()),
        "AGENT_CANVAS_BASE_PATH": "/",
        "XDG_CONFIG_HOME": str(Path.home() / ".config"),
        "OH_ENABLE_VSCODE": "false",
        # No product analytics to OpenHands (page, automation, agent server).
        "AGENT_CANVAS_DISABLE_TELEMETRY": "1",
        "VITE_DO_NOT_TRACK": "1",
        "DO_NOT_TRACK": "1",
        "OH_SESSION_API_KEYS_0": session_key,
    }
    # The OmicsBase gateway; it also stands in for VS Code with OmicsBase's own editor.
    env["OMICSBASE_GATEWAY_PORT"] = env["VSCODE_PORT"] = str(free_port())
    os.execvpe(ENTRYPOINT, [ENTRYPOINT], env)


def serve(argv):
    """Wait for an OmicsBase install to dial in: a tunnel server whose credential opens only
    the agent, and the pairing string that tells the install how to reach it."""
    parser = argparse.ArgumentParser(prog="serve", description=serve.__doc__.splitlines()[0])
    parser.add_argument("--address", required=True,
                        help="the name or IP address at which the OmicsBase install reaches this machine")
    parser.add_argument("--port", type=int, default=SERVE_PORT,
                        help="the port published for it (docker run -p <port>:8443)")
    args = parser.parse_args(argv)

    state = HOME / "serve.json"
    if state.exists():
        s = json.loads(state.read_text())
    else:
        s = {"password": secrets.token_hex(24), "sessionKey": secrets.token_hex(24), "target": free_port()}
        private_file(state, json.dumps(s))
    key = HOME / "serve.key"
    if not key.exists():
        subprocess.run(["chisel", "server", "--keygen", str(key)], check=True, capture_output=True)
        key.chmod(0o600)
    users = HOME / "serve-users.json"
    private_file(users, json.dumps({f"machine:{s['password']}": [f"^127\\.0\\.0\\.1:{s['target']}$"]}))

    log = HOME / "serve.log"
    with open(log, "w") as out:
        subprocess.Popen(
            ["chisel", "server", "--host", "0.0.0.0", "--port", str(SERVE_PORT),
             "--authfile", str(users), "--keyfile", str(key)],
            stdout=out, stderr=subprocess.STDOUT,
        )
    fingerprint = None
    for _ in range(100):
        found = [line.split("Fingerprint ", 1)[1].strip() for line in log.read_text().splitlines()
                 if "Fingerprint " in line]
        if found:
            fingerprint = found[0]
            break
        time.sleep(0.1)
    if fingerprint is None:
        fail(f"the tunnel server did not start: {log.read_text()[-500:]}")

    pairing = {"address": args.address, "port": args.port, "fingerprint": fingerprint,
               "auth": f"machine:{s['password']}", "sessionKey": s["sessionKey"], "target": s["target"]}
    code = MACHINE_PREFIX + base64.urlsafe_b64encode(json.dumps(pairing).encode()).decode().rstrip("=")
    print("OmicsBase Reports: paste this in Reports, under Use my own computer:\n\n"
          f"{code}\n\nKeep it private: it opens this machine's agent.", flush=True)
    start_agent(s["sessionKey"], s["target"])


def main():
    if sys.argv[1:2] == ["serve"]:
        return serve(sys.argv[2:])
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("app", help="the OmicsBase address, e.g. https://omicsbase.utu.fi")
    parser.add_argument("--pair", help="the one-time pairing code shown in Reports")
    parser.add_argument("--name", default="", help="how this machine is shown in Reports")
    args = parser.parse_args()
    app = args.app.rstrip("/")
    # The code and this machine's credentials travel to that address: HTTPS, except for an
    # OmicsBase on this same machine.
    if not app.startswith("https://") and urllib.parse.urlsplit(app).hostname not in ("localhost", "127.0.0.1"):
        fail("the OmicsBase address must start with https://")

    s = settings(app, args.pair, args.name)
    port = free_port()
    # chisel reads the relay credential from AUTH, so it never appears in the process list,
    # and keeps reconnecting if the connection drops.
    subprocess.Popen(
        ["chisel", "client", "--fingerprint", s["relayFingerprint"], s["relayUrl"],
         f"{s['relayRemote']}:127.0.0.1:{port}"],
        env={"PATH": os.environ.get("PATH", ""), "AUTH": s["relayAuth"]},
    )
    print(f"OmicsBase Reports: this machine is paired with {app}; open Reports there.", flush=True)
    start_agent(s["sessionKey"], port)


if __name__ == "__main__":
    main()
