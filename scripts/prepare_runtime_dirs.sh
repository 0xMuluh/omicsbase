#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

UID_VALUE="${UID_VALUE:-$(id -u)}"
GID_VALUE="${GID_VALUE:-$(id -g)}"

dirs=(
  "$ROOT/librechat/logs"
  "$ROOT/librechat/uploads"
  "$ROOT/librechat/images"
  "$ROOT/librechat/data-node"
  "$ROOT/librechat/meili_data_v1.35.1"
)

echo "Preparing runtime directories for ${UID_VALUE}:${GID_VALUE}"

sudo mkdir -p "${dirs[@]}"
sudo chown -R "${UID_VALUE}:${GID_VALUE}" "${dirs[@]}"
sudo chmod -R u+rwX "${dirs[@]}"

# Engine and LibreChat share analysis working copies. Preserve file owners.
# setgid makes new directories inherit the shared group, including root-created ones.
sudo mkdir -p "$ROOT/projects"
sudo find "$ROOT/projects" -type d -exec chgrp "$GID_VALUE" {} + -exec chmod g+rws {} +
sudo find "$ROOT/projects" -type f -exec chgrp "$GID_VALUE" {} + -exec chmod g+rw {} +

echo "Runtime directories prepared."
