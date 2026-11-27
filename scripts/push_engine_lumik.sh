#!/bin/sh
# Make a released engine image runnable on LUMI-K and push it to LUMI-K's registry.
# Run on the VM as root, after CI has pushed the release and you have signed in to the
# LUMI-K registry (docs/develop/production.qmd#engine):
#
#   sudo scripts/push_engine_lumik.sh 2026.11.28
set -eu
VERSION=$1
ENGINE=ghcr.io/0xmuluh/omicsbase-engine:$VERSION
LUMIK=registry.apps.v1.lumi-k.eu/omicsbase-utu/omicsbase-engine:lumik-$VERSION
cd "$(dirname "$0")/../deployment/rahti"
docker pull -q "$ENGINE"
docker build -q --build-arg ENGINE_IMAGE="$ENGINE" -t "$LUMIK" .
# LUMI-K runs the container as a random user in group 0.
docker run --rm --user 1000770000:0 --entrypoint sh "$LUMIK" -c \
  'set -e; cd /app; python3 -c "import engine.server"; test -w /data/projects; Rscript -e "library(mia)" >/dev/null'
docker push -q "$LUMIK"
echo "$LUMIK"
