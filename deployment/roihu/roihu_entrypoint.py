"""Adapt upstream's Agent Canvas entrypoint for a per-user Reports job on Roihu.

On a shared compute node every service must listen on 127.0.0.1 only, and the page
server must ask for the session key instead of writing it into the page. Each change
is anchored on a line of upstream's script; if upstream moves one, this stops instead
of producing an entrypoint that listens on all interfaces.

    python3 roihu_entrypoint.py <upstream entrypoint.sh> <output>
"""
import sys

CHANGES = [
    # Agent server and automation server.
    ("    --host 0.0.0.0 \\\n", "    --host 127.0.0.1 \\\n", 2),
    # Page server, IPv6 default and its IPv4 fallback.
    ('STATIC_SERVER_BIND_HOST="::"\n', 'STATIC_SERVER_BIND_HOST="127.0.0.1"\n', 1),
    ('  STATIC_SERVER_BIND_HOST="0.0.0.0"\n', '  STATIC_SERVER_BIND_HOST="127.0.0.1"\n', 1),
    ('  --port "$PORT" \\\n  --host "$STATIC_SERVER_BIND_HOST" \\\n',
     '  --port "$PORT" \\\n  --host "$STATIC_SERVER_BIND_HOST" --auth-required \\\n', 1),
]


def adapt(script: str) -> str:
    for old, new, count in CHANGES:
        found = script.count(old)
        if found != count:
            sys.exit(f"roihu entrypoint: expected {count} of {old!r}, found {found}")
        script = script.replace(old, new)
    # Any remaining wildcard address outside comments and log messages is a bind we missed.
    for line in script.splitlines():
        code = line.strip()
        if code.startswith("#") or code.startswith("log "):
            continue
        if "0.0.0.0" in code or '"::"' in code:
            sys.exit(f"roihu entrypoint: wildcard address left in: {code}")
    return script


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    with open(src) as f:
        adapted = adapt(f.read())
    with open(dst, "w") as f:
        f.write(adapted)
