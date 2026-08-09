#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 1 ]]; then
  echo "Usage: validate_bundle.sh <output-dir-or-zip>" >&2
  exit 2
fi
TARGET="$1"
unsafe_re='\.(php|phtml|phar|cgi)$'
if [[ -d "$TARGET" ]]; then
  if [[ ! -f "$TARGET/index.html" ]]; then
    echo "bundle_failed: missing root index.html" >&2
    exit 1
  fi
  if find "$TARGET" -type f | grep -Eiq "$unsafe_re"; then
    echo "bundle_failed: server-executable file found" >&2
    find "$TARGET" -type f | grep -Ei "$unsafe_re" >&2
    exit 1
  fi
  echo "bundle_ok: directory $TARGET"
  exit 0
fi
if [[ -f "$TARGET" && "$TARGET" == *.zip ]]; then
  if ! command -v unzip >/dev/null 2>&1; then
    echo "bundle_failed: unzip command not found" >&2
    exit 1
  fi
  listing="$(unzip -Z1 "$TARGET")"
  if ! printf '%s
' "$listing" | grep -qx 'index.html'; then
    echo "bundle_failed: zip missing root index.html" >&2
    exit 1
  fi
  if printf '%s
' "$listing" | grep -Eq '(^/|(^|/)\.\.(/|$))'; then
    echo "bundle_failed: unsafe zip path" >&2
    printf '%s
' "$listing" | grep -E '(^/|(^|/)\.\.(/|$))' >&2
    exit 1
  fi
  if printf '%s
' "$listing" | grep -Eiq "$unsafe_re"; then
    echo "bundle_failed: server-executable file found" >&2
    printf '%s
' "$listing" | grep -Ei "$unsafe_re" >&2
    exit 1
  fi
  echo "bundle_ok: zip $TARGET"
  exit 0
fi
echo "bundle_failed: target is not a directory or .zip file: $TARGET" >&2
exit 1
