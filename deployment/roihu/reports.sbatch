#!/bin/bash
# One user's Reports agent as a Slurm job on Roihu, running as that user.
#
# OmicsBase submits this script through FirecREST with the user's token; account, partition,
# cores, memory and time come with the submission, and these environment variables:
#   REPORTS_IMAGE      Apptainer image built from deployment/roihu/Dockerfile
#   REPORTS_STATE      the user's own folder: OpenHands settings, conversations, project files
#   SESSION_KEY        the agent's session key; Caddy adds it to the user's requests
#   RELAY_URL, RELAY_FINGERPRINT, RELAY_REMOTE, RELAY_AUTH
#                      outbound HTTPS tunnel to the OmicsBase relay (roihu-relay service), so
#                      the job is reachable through openhands.omicsbase.utu.fi
set -euo pipefail
umask 077

image=$REPORTS_IMAGE
state=$REPORTS_STATE
if [ ! -f "$image" ]; then
  echo "Reports image $image is missing; the preparation job did not finish." >&2
  exit 1
fi
mkdir -p "$state/home" "$state/projects"

freeport() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'; }
export APPTAINERENV_PORT=$(freeport)
export APPTAINERENV_AGENT_SERVER_PORT=$(freeport)
export APPTAINERENV_AUTOMATION_PORT=$(freeport)
# The OmicsBase gateway; it also stands in for VS Code with OmicsBase's own editor.
export APPTAINERENV_OMICSBASE_GATEWAY_PORT=$(freeport)
export APPTAINERENV_VSCODE_PORT=$APPTAINERENV_OMICSBASE_GATEWAY_PORT
export APPTAINERENV_OH_ENABLE_VSCODE=false
export APPTAINERENV_AGENT_CANVAS_BASE_PATH=/
export APPTAINERENV_XDG_CONFIG_HOME=/home/openhands/.config
# No product analytics to OpenHands from users' sessions (page, automation, agent server).
export APPTAINERENV_AGENT_CANVAS_DISABLE_TELEMETRY=1
export APPTAINERENV_VITE_DO_NOT_TRACK=1
export APPTAINERENV_DO_NOT_TRACK=1
# Upstream's entrypoint passes only OH_SESSION_API_KEYS_0 to the agent server.
export APPTAINERENV_OH_SESSION_API_KEYS_0=$SESSION_KEY

# On CSC machines the agent loads CSC's bio-apps tools with `module load` inside the image,
# from the host's Lmod, software (/appl) and reference databases (/dataset).
csc=()
if [ -d /usr/share/lmod ] && [ -d /appl/modulefiles ]; then
  csc=(--bind /appl --bind /dataset --bind /usr/share/lmod --bind /usr/bin/lua:/usr/bin/lua)
  for d in /usr/lib64/lua /usr/share/lua; do [ -d "$d" ] && csc+=(--bind "$d"); done
  # Lua for Lmod, libcrypt for Perl tools, liblustreapi and what it links to for HDF5. Only
  # those the image lacks: a host copy on LD_LIBRARY_PATH would shadow the image's own.
  have=$(apptainer exec "$image" ldconfig -p | awk '{print $1}')
  for f in /usr/lib64/liblua-5.4.so /usr/lib64/libcrypt.so.2 /usr/lib64/liblustreapi.so.1 \
      $(ldd /usr/lib64/liblustreapi.so.1 | awk '$3 ~ /^\// {print $3}' | grep -v -E '/(libc|libm|libpthread|libdl|librt|ld-linux)[.-]'); do
    grep -qxF "${f##*/}" <<< "$have" || csc+=(--bind "$(readlink -f "$f"):/opt/hostlibs/${f##*/}")
  done
  # Lmod's settings from CSC's login profile, read in a subshell: the profile uses unset
  # variables and sets its own umask.
  eval "$(set +eu; export CSC_ENV_INIT_NON_INTERACTIVE=yes; source /etc/profile > /dev/null 2>&1
    for v in MODULEPATH LMOD_CMD LMOD_DIR LMOD_PKG LMOD_SYSTEM_DEFAULT_MODULES; do
      printf 'export APPTAINERENV_%s=%q\n' "$v" "${!v}"; done)"
  # BASH_ENV gives non-interactive shells the image's `module` function too.
  export APPTAINERENV_LD_LIBRARY_PATH=/opt/hostlibs APPTAINERENV_OMICSBASE_CSC_MODULES=1 \
    APPTAINERENV_BASH_ENV=/etc/profile.d/omicsbase-csc-modules.sh
fi

echo "Reports agent on $(hostname):$APPTAINERENV_PORT"

# chisel reads the relay credential from AUTH, so it never appears in the process list.
APPTAINERENV_AUTH=$RELAY_AUTH apptainer exec --cleanenv "$image" chisel client \
  --fingerprint "$RELAY_FINGERPRINT" "$RELAY_URL" \
  "$RELAY_REMOTE:127.0.0.1:$APPTAINERENV_PORT" > "$state/relay.log" 2>&1 &
unset RELAY_AUTH SESSION_KEY

exec apptainer exec --cleanenv --writable-tmpfs \
  --home "$state/home:/home/openhands" \
  --bind "$state/projects:/projects" "${csc[@]}" \
  "$image" /opt/agent-canvas/entrypoint.roihu.sh
