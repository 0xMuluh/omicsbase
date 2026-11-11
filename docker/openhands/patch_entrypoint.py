"""Apply OmicsBase's changes to the upstream Agent Canvas entrypoint at image build time.

Carrying a full copy of upstream's entrypoint drifted from it with every release, so the
image now patches upstream's own script. Each change is anchored on a line of upstream's
script; if upstream moves one, the build stops here instead of shipping a silently
broken entrypoint. Editor routing (VSCODE_PORT=18002, OH_ENABLE_VSCODE=false) and the
session-key opt-in (AGENT_CANVAS_ALLOW_LAN_SESSION_KEY) need no patch: they are set in
the compose environment.
"""
import sys

GATEWAY_PORT = 18002


def patch(script: str) -> str:
    def insert_after(anchor, text, count=1):
        nonlocal script
        found = script.count(anchor)
        if found != count:
            sys.exit(f"entrypoint patch: expected {count} of {anchor!r}, found {found}")
        script = script.replace(anchor, anchor + text)

    def insert_before(anchor, text):
        nonlocal script
        if script.count(anchor) != 1:
            sys.exit(f"entrypoint patch: expected one {anchor!r}")
        script = script.replace(anchor, text + anchor)

    # Project folders are shared with the engine, which runs as another user.
    insert_after("log() { printf '[agent-canvas] %s\\n' \"$*\"; }\n",
                 "# OmicsBase: files in shared project folders stay writable for the engine.\numask 000\n")

    insert_before("# ── 3. Wait for backends to be ready", f"""# ── 2b. Start the OmicsBase gateway ─────────────────────────────────────────
log "Starting OmicsBase gateway on port {GATEWAY_PORT}..."
export PYTHONPATH="/opt/agent-canvas:${{PYTHONPATH:-}}"
python3 -m uvicorn gateway.app:app --host 127.0.0.1 --port {GATEWAY_PORT} &
PIDS+=($!)

""")
    insert_after('wait "$WAIT_PID2"\n', f'wait_for_port {GATEWAY_PORT} "OmicsBase Gateway" 30\n')

    # The gateway serves OmicsBase's own API, conversation listing and the editor.
    routes = "".join(
        f'{{indent}}--route "{prefix}=http://127.0.0.1:{GATEWAY_PORT}" \\\n'
        for prefix in ("/api/conversations", "/api/omicsbase", "/api/vscode")
    )
    automation = '--route "/api/automation=http://127.0.0.1:${AUTOMATION_PORT}" \\\n'
    lines = script.splitlines(keepends=True)
    out, added = [], 0
    for line in lines:
        out.append(line)
        if line.strip() + "\n" == automation:
            indent = line[: len(line) - len(line.lstrip())]
            out.append(routes.format(indent=indent))
            added += 1
    if added != 2:
        sys.exit(f"entrypoint patch: expected 2 static-server route lists, found {added}")
    return "".join(out)


if __name__ == "__main__":
    path = sys.argv[1]
    with open(path) as f:
        patched = patch(f.read())
    with open(path, "w") as f:
        f.write(patched)
    print(f"patched {path}")
