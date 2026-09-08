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

echo "Runtime directories prepared."
