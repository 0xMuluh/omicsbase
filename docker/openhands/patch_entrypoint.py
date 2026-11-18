"""Apply OmicsBase's changes to the upstream Agent Canvas entrypoint at image build time.

Carrying a full copy of upstream's entrypoint drifted from it with every release, so the
image now patches upstream's own script. Each change is anchored on a line of upstream's
script; if upstream moves one, the build stops here instead of shipping a silently
broken entrypoint. Editor routing (VSCODE_PORT=18002, OH_ENABLE_VSCODE=false) and the
session-key opt-in (AGENT_CANVAS_ALLOW_LAN_SESSION_KEY) need no patch: they are set in
the compose environment.
"""
import sys

GATEWAY_PORT = "18002"
# What the gateway serves on the VM: OmicsBase's own API, conversation listing and the editor.
VM_ROUTES = ("/api/conversations", "/api/omicsbase", "/api/vscode")


def _insert_after(script, anchor, text, count=1):
    found = script.count(anchor)
    if found != count:
        sys.exit(f"entrypoint patch: expected {count} of {anchor!r}, found {found}")
    return script.replace(anchor, anchor + text)


def add_gateway(script: str, port: str = GATEWAY_PORT, prefixes=VM_ROUTES) -> str:
    """Start the OmicsBase gateway on 127.0.0.1:<port> and route <prefixes> to it.

    <port> is put into the script as is, so it may be a shell expression such as
    "${OMICSBASE_GATEWAY_PORT}".
    """
    anchor = "# ── 3. Wait for backends to be ready"
    if script.count(anchor) != 1:
        sys.exit(f"entrypoint patch: expected one {anchor!r}")
    script = script.replace(anchor, f"""# ── 2b. Start the OmicsBase gateway ─────────────────────────────────────────
log "Starting OmicsBase gateway on port {port}..."
export PYTHONPATH="/opt/agent-canvas:${{PYTHONPATH:-}}"
python3 -m uvicorn gateway.app:app --host 127.0.0.1 --port {port} &
PIDS+=($!)

""" + anchor)
    script = _insert_after(script, 'wait "$WAIT_PID2"\n', f'wait_for_port {port} "OmicsBase Gateway" 30\n')

    automation = '--route "/api/automation=http://127.0.0.1:${AUTOMATION_PORT}" \\\n'
    out, added = [], 0
    for line in script.splitlines(keepends=True):
        out.append(line)
        if line.strip() + "\n" == automation:
            indent = line[: len(line) - len(line.lstrip())]
            out += [f'{indent}--route "{prefix}=http://127.0.0.1:{port}" \\\n' for prefix in prefixes]
            added += 1
    if added != 2:
        sys.exit(f"entrypoint patch: expected 2 static-server route lists, found {added}")
    return "".join(out)


def patch(script: str) -> str:
    # Project folders are shared with the engine, which runs as another user.
    script = _insert_after(script, "log() { printf '[agent-canvas] %s\\n' \"$*\"; }\n",
                           "# OmicsBase: files in shared project folders stay writable for the engine.\numask 000\n")
    return add_gateway(script)


if __name__ == "__main__":
    path = sys.argv[1]
    with open(path) as f:
        patched = patch(f.read())
    with open(path, "w") as f:
        f.write(patched)
    print(f"patched {path}")
