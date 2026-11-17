#!/bin/bash
# One user's Reports agent as a Slurm job on Roihu, running as that user.
#
#   sbatch --account=<project> --time=<hh:mm:ss> --cpus-per-task=<n> --mem=<size> \
#     reports.sbatch <image.sif> <state folder>
#
# <image.sif> is built from deployment/roihu/Dockerfile: it carries the tunnel client and
# upstream's entrypoint adapted for Roihu (entrypoint.roihu.sh). The state folder keeps the
# user's OpenHands settings, conversations and project files between jobs. The connection
# details (node, port, session key) are written to <state folder>/connect.txt, readable by
# the user only.
#
# Optional: ROUTE_ENV, the route file from roihu_router.py (tunnel port, relay credential,
# session key), with RELAY_URL and RELAY_FINGERPRINT, opens an outbound HTTPS
# tunnel to the OmicsBase relay so the job is reachable through openhands.omicsbase.utu.fi.
#SBATCH --partition=interactive
#SBATCH --job-name=omicsbase-reports
#SBATCH --output=%x-%j.out
set -euo pipefail
umask 077

image=$1
state=$2
mkdir -p "$state/home" "$state/projects"

freeport() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'; }
export APPTAINERENV_PORT=$(freeport)
export APPTAINERENV_AGENT_SERVER_PORT=$(freeport)
export APPTAINERENV_AUTOMATION_PORT=$(freeport)
export APPTAINERENV_VSCODE_PORT=$(freeport)
export APPTAINERENV_OH_ENABLE_VSCODE=false
export APPTAINERENV_AGENT_CANVAS_BASE_PATH=/
export APPTAINERENV_XDG_CONFIG_HOME=/home/openhands/.config
# No product analytics to OpenHands from users' sessions (page, automation, agent server).
export APPTAINERENV_AGENT_CANVAS_DISABLE_TELEMETRY=1
export APPTAINERENV_VITE_DO_NOT_TRACK=1
export APPTAINERENV_DO_NOT_TRACK=1
if [ -n "${ROUTE_ENV:-}" ]; then
  RELAY_REMOTE=$(grep '^RELAY_REMOTE=' "$ROUTE_ENV" | cut -d= -f2-)
  SESSION_KEY=$(grep '^SESSION_KEY=' "$ROUTE_ENV" | cut -d= -f2-)
fi
# Upstream's entrypoint passes only OH_SESSION_API_KEYS_0 to the agent server.
export APPTAINERENV_OH_SESSION_API_KEYS_0=${SESSION_KEY:-$(openssl rand -hex 24)}

printf 'job=%s\nnode=%s\nport=%s\nkey=%s\n' "$SLURM_JOB_ID" "$(hostname)" \
  "$APPTAINERENV_PORT" "$APPTAINERENV_OH_SESSION_API_KEYS_0" > "$state/connect.txt"
echo "Reports agent on $(hostname):$APPTAINERENV_PORT; key in $state/connect.txt"

if [ -n "${ROUTE_ENV:-}" ]; then
  # chisel reads the relay credential from AUTH, so it never appears in the process list.
  APPTAINERENV_AUTH=$(grep '^RELAY_AUTH=' "$ROUTE_ENV" | cut -d= -f2-) \
    apptainer exec --cleanenv "$image" chisel client \
    --fingerprint "$RELAY_FINGERPRINT" "$RELAY_URL" \
    "$RELAY_REMOTE:127.0.0.1:$APPTAINERENV_PORT" > "$state/relay.log" 2>&1 &
fi

exec apptainer exec --cleanenv --writable-tmpfs \
  --home "$state/home:/home/openhands" \
  --bind "$state/projects:/projects" \
  "$image" /opt/agent-canvas/entrypoint.roihu.sh
