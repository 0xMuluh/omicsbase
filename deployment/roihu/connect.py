"""Make this machine the Reports computer of an OmicsBase account ("Use my own computer").

    docker run -d --restart unless-stopped --name omicsbase-reports \\
      -v omicsbase-reports:/home/openhands -v omicsbase-projects:/projects \\
      <this image> https://omicsbase.utu.fi --pair ABCDE-FGHJK --name "$(hostname)"

The first start trades the one-time pairing code for this machine's relay credential and
session key, kept in the home volume; later starts (a restart, a reboot) reuse them. Then the
outbound tunnel and the agent start as in a Roihu job (reports.sbatch), listening on
127.0.0.1 only. Nothing needs to reach this machine from outside.
"""
import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

STATE = Path.home() / ".omicsbase" / "attach.json"
ENTRYPOINT = "/opt/agent-canvas/entrypoint.roihu.sh"


def fail(message):
    sys.exit(f"OmicsBase Reports: {message}")


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
            fail("this pairing code is unknown, used or expired. Get a new one in Reports.")
        if e.code == 409:
            fail("you already have a Reports session running. Stop it in Reports, then get a new code.")
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
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.touch(mode=0o600)
    STATE.write_text(json.dumps(saved))
    return saved


def main():
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
    env = {
        **os.environ,
        "PORT": str(free_port()),
        "AGENT_SERVER_PORT": str(free_port()),
        "AUTOMATION_PORT": str(free_port()),
        "AGENT_CANVAS_BASE_PATH": "/",
        "XDG_CONFIG_HOME": str(Path.home() / ".config"),
        "OH_ENABLE_VSCODE": "false",
        # No product analytics to OpenHands (page, automation, agent server).
        "AGENT_CANVAS_DISABLE_TELEMETRY": "1",
        "VITE_DO_NOT_TRACK": "1",
        "DO_NOT_TRACK": "1",
        "OH_SESSION_API_KEYS_0": s["sessionKey"],
    }
    # The OmicsBase gateway; it also stands in for VS Code with OmicsBase's own editor.
    env["OMICSBASE_GATEWAY_PORT"] = env["VSCODE_PORT"] = str(free_port())

    # chisel reads the relay credential from AUTH, so it never appears in the process list,
    # and keeps reconnecting if the connection drops.
    subprocess.Popen(
        ["chisel", "client", "--fingerprint", s["relayFingerprint"], s["relayUrl"],
         f"{s['relayRemote']}:127.0.0.1:{env['PORT']}"],
        env={"PATH": os.environ.get("PATH", ""), "AUTH": s["relayAuth"]},
    )
    print(f"OmicsBase Reports: this machine is paired with {app}; open Reports there.", flush=True)
    os.execvpe(ENTRYPOINT, [ENTRYPOINT], env)


if __name__ == "__main__":
    main()
