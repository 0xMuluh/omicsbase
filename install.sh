#!/bin/sh
# Run OmicsBase on this machine, with nothing to configure:
#
#   curl -fsSL https://raw.githubusercontent.com/0xMuluh/omicsbase/main/install.sh | sh
#
# Installs into ~/omicsbase (OMICSBASE_DIR to change it) and opens on http://localhost:3080,
# reachable only from this machine. Running it again updates the files and images and keeps
# your accounts, notes and settings (data/ and .env).
set -eu

REPO=${OMICSBASE_REPO:-0xMuluh/omicsbase}
REF=${OMICSBASE_REF:-main}
DIR=${OMICSBASE_DIR:-$HOME/omicsbase}
VERSION=${OMICSBASE_VERSION:-latest}
EXAMPLES=${OMICSBASE_EXAMPLES:-examples-2026-11-20}
RAW="https://raw.githubusercontent.com/$REPO/$REF/local"
APP=http://localhost:3080

say() { printf '%s\n' "$*"; }
warn() { printf 'Note: %s\n' "$*" >&2; }
fail() { printf 'OmicsBase was not installed: %s\n' "$*" >&2; exit 1; }

# 1. What it needs: Docker with Compose, running and usable by this user.
command -v curl >/dev/null 2>&1 || fail "curl is not installed."
command -v docker >/dev/null 2>&1 || fail "Docker is not installed. Get it from https://docs.docker.com/get-docker/ and run this again."
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is missing (the 'docker compose' command). It comes with Docker Desktop and the docker-compose-plugin package."
docker info >/dev/null 2>&1 || fail "Docker is installed but not running, or this user may not use it (on Linux: add yourself to the docker group, then log in again)."

# About 8 GB to download and 30 GB on disk; notes run R on this machine.
mkdir -p "$DIR"
free_gb=$(df -Pk "$DIR" | awk 'NR == 2 { print int($4 / 1048576) }')
[ "${free_gb:-0}" -ge 30 ] || warn "only ${free_gb} GB free where OmicsBase goes ($DIR); it needs about 30 GB."
mem_gb=$(awk '/MemTotal/ { print int($2 / 1048576) }' /proc/meminfo 2>/dev/null || true)
[ -n "$mem_gb" ] || mem_gb=$(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1073741824 ))
[ "$mem_gb" -ge 8 ] || warn "this machine has ${mem_gb} GB of memory; 8 GB or more is recommended."

# 2. The compose files, fetched fresh each time.
cd "$DIR"
say "Installing OmicsBase in $DIR"
for f in docker-compose.yml librechat.yaml reports-gate.Caddyfile import_examples.js .env.example; do
  curl -fsSL "$RAW/$f" -o "$f" || fail "could not download $f from $RAW."
done

# 3. Settings: generated once and kept on later runs; secrets that a newer version needs
#    are added to an existing .env.
secret() { openssl rand -hex 32 2>/dev/null || od -An -tx1 -N32 /dev/urandom | tr -d ' \n'; }
if [ ! -f .env ]; then
  sed -e "s/^OMICSBASE_VERSION=.*/OMICSBASE_VERSION=$VERSION/" \
      -e "s/^UID=.*/UID=$(id -u)/" \
      -e "s/^GID=.*/GID=$(id -g)/" \
      .env.example > .env
  chmod 600 .env
fi
for name in OMICSBASE_AUTH_SECRET OMICSBASE_ENGINE_SECRET OMICSBASE_LINK_SECRET MEILI_MASTER_KEY; do
  if grep -q "^$name=\$" .env; then
    sed -i.bak "s/^$name=\$/$name=$(secret)/" .env && rm -f .env.bak
  elif ! grep -q "^$name=" .env; then
    printf '%s=%s\n' "$name" "$(secret)" >> .env
  fi
done

# 4. Data folders, created as this user. Reports runs as another user in your group, so the
#    folders it shares are group-writable.
for d in app uploads images logs projects mongodb meilisearch engine-cache reports reports-link; do
  mkdir -p "data/$d"
done
chmod 2775 data/projects data/reports

# 5. Example notes for the landing page, checked against their published checksum.
if [ ! -d examples/db ]; then
  url="https://github.com/$REPO/releases/download/$EXAMPLES"
  tmp=$(mktemp -d)
  if curl -fsSL "$url/omicsbase-examples.tgz" -o "$tmp/examples.tgz" &&
     curl -fsSL "$url/omicsbase-examples.tgz.sha256" -o "$tmp/examples.sha256"; then
    want=$(awk '{ print $1 }' "$tmp/examples.sha256")
    got=$( (sha256sum "$tmp/examples.tgz" 2>/dev/null || shasum -a 256 "$tmp/examples.tgz") | awk '{ print $1 }')
    if [ "$want" = "$got" ]; then
      mkdir -p examples && tar xzf "$tmp/examples.tgz" -C examples --strip-components=1
    else
      warn "the example notes did not match their checksum and were skipped."
    fi
  else
    warn "the example notes could not be downloaded; OmicsBase works without them."
  fi
  rm -rf "$tmp"
fi
mkdir -p examples

# 6. Start. Docker Compose knows this install as the project "omicsbase"; another project of
#    that name in a different folder would be taken over, so stop instead.
other=$(docker ps -a --filter label=com.docker.compose.project=omicsbase \
  --format '{{.Label "com.docker.compose.project.working_dir"}}' | sort -u | grep -vxF -e "$PWD" -e "$(pwd -P)" || true)
[ -z "$other" ] || fail "Docker already has a project called \"omicsbase\", from $other. Nothing was changed. Remove it first (cd $other && docker compose down), then run this again."
say "Downloading the images (about 8 GB the first time)..."
docker compose pull --quiet || fail "the images could not be downloaded (see the message above). Check that Docker is still running, then run the install command again; it continues where it stopped."
docker compose up -d || fail "the services did not start. See: cd $DIR && docker compose logs"

say "Starting (about a minute)..."
i=0
until curl -fs -o /dev/null "$APP/health" 2>/dev/null || curl -fs -o /dev/null "$APP" 2>/dev/null; do
  i=$((i + 1))
  [ "$i" -lt 60 ] || fail "it did not answer on $APP after 5 minutes. See: cd $DIR && docker compose logs api"
  sleep 5
done

cat <<EOF

OmicsBase is running: $APP

  Create an account there; the first one becomes the admin.
  Bring a model: paste a key (OpenRouter, OpenAI, Anthropic or Google) when you pick a
  model, or start a model in Ollama on this machine.

  Stop:    cd $DIR && docker compose down
  Update:  run the install command again
EOF
