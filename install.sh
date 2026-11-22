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
DIR=${OMICSBASE_DIR:-$HOME/omicsbase}
# A version asked for here (OMICSBASE_VERSION=2026.11.22) is also written into an existing .env.
PIN=${OMICSBASE_VERSION:-}
VERSION=${PIN:-latest}
EXAMPLES=${OMICSBASE_EXAMPLES:-examples-2026-11-20}
APP=http://localhost:3080

say() { printf '%s\n' "$*"; }
warn() { printf 'Note: %s\n' "$*" >&2; }
fail() { printf 'OmicsBase was not installed: %s\n' "$*" >&2; exit 1; }

# 1. What it needs: Docker with Compose, running and usable by this user.
command -v curl >/dev/null 2>&1 || fail "curl is not installed."
command -v docker >/dev/null 2>&1 || fail "Docker is not installed. Get it from https://docs.docker.com/get-docker/ and run this again."
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is missing (the 'docker compose' command). It comes with Docker Desktop and the docker-compose-plugin package."
docker info >/dev/null 2>&1 || fail "Docker is installed but not running, or this user may not use it (on Linux: add yourself to the docker group, then log in again)."

# About 8 GB to download: the images take about 25 GB where Docker keeps them, and your
# notes and data live in $DIR.
mkdir -p "$DIR"
free_gb() { gb=$(df -Pk "$1" 2>/dev/null | awk 'NR == 2 { print int($4 / 1048576) }'); echo "${gb:-0}"; }
device() { df -P "$1" 2>/dev/null | awk 'NR == 2 { print $1 }'; }
docker_dir=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)
if [ -n "$docker_dir" ] && [ -d "$docker_dir" ] && [ "$(device "$docker_dir")" != "$(device "$DIR")" ]; then
  [ "$(free_gb "$docker_dir")" -ge 25 ] || warn "only $(free_gb "$docker_dir") GB free where Docker keeps its images ($docker_dir); the images need about 25 GB."
  [ "$(free_gb "$DIR")" -ge 5 ] || warn "only $(free_gb "$DIR") GB free where OmicsBase keeps your notes ($DIR); give it at least 5 GB."
else
  # Same disk, or Docker keeps its images inside its own virtual machine (Docker Desktop),
  # where this script cannot see the free space.
  [ "$(free_gb "$DIR")" -ge 30 ] || warn "only $(free_gb "$DIR") GB free where OmicsBase goes ($DIR); it needs about 30 GB."
fi
# The memory Docker may use: on Mac and Windows, Docker Desktop's own limit, not the machine's.
# Rounded, since an 8 GB machine reports a little less.
mem_bytes=$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo 0)
mem_gb=$(( (${mem_bytes:-0} + 536870912) / 1073741824 ))
[ "$mem_gb" -ge 8 ] || warn "Docker may use ${mem_gb} GB of memory; 8 GB or more is recommended. With Docker Desktop, raise it under Settings > Resources > Memory."
case "$(docker info --format '{{.Architecture}}' 2>/dev/null)" in
  aarch64|arm64) warn "this is an ARM machine (such as an Apple-silicon Mac). The OmicsBase images are built for Intel/AMD, so Docker runs them emulated: expect them to be slow. With Docker Desktop, turn on Settings > General > Use Rosetta." ;;
esac
# Ollama on Linux listens only on 127.0.0.1 unless told otherwise, and the containers cannot reach that.
if [ "$(uname -s)" = Linux ] && command -v ollama >/dev/null 2>&1 &&
   curl -fs -o /dev/null http://127.0.0.1:11434/ 2>/dev/null &&
   ! curl -fs -o /dev/null --max-time 2 "http://$(docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || echo 172.17.0.1):11434/" 2>/dev/null; then
  warn "Ollama is running but only for this machine's own programs, so OmicsBase cannot use it. To allow it, run 'sudo systemctl edit ollama', add the two lines [Service] and Environment=\"OLLAMA_HOST=0.0.0.0\", then 'sudo systemctl restart ollama'. This also lets other computers on your network reach Ollama unless a firewall blocks port 11434."
fi

# 2. The compose files, fetched fresh each time: from the release of the pinned version (asked
#    for now, or kept in .env from an earlier run), or main.
REF=${OMICSBASE_REF:-}
pinned=$PIN
[ -n "$pinned" ] || pinned=$(sed -n 's/^OMICSBASE_VERSION=//p' "$DIR/.env" 2>/dev/null || true)
if [ -z "$REF" ] && [ -n "$pinned" ] && [ "$pinned" != latest ]; then
  if curl -fsI "https://raw.githubusercontent.com/$REPO/$pinned/local/docker-compose.yml" >/dev/null 2>&1; then
    REF=$pinned
  else
    warn "no files are released under $pinned, so the current ones are used with the $pinned images."
  fi
fi
RAW="https://raw.githubusercontent.com/$REPO/${REF:-main}/local"
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
elif [ -n "$PIN" ]; then
  sed -i.bak "s/^OMICSBASE_VERSION=.*/OMICSBASE_VERSION=$PIN/" .env && rm -f .env.bak
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
say "Downloading the images (about 8 GB the first time; this can take a while)..."
docker compose pull || fail "the images could not be downloaded (see the message above). Check that Docker is still running, then run the install command again; it continues where it stopped."
docker compose up -d || fail "the services did not start. See: cd $DIR && docker compose logs"

say "Starting (about a minute)..."
# Wait up to 5 minutes, with a line every 15 s. A slow start is not a failed install.
s=0
ready=yes
until curl -fs -o /dev/null "$APP/health" 2>/dev/null || curl -fs -o /dev/null "$APP" 2>/dev/null; do
  sleep 5
  s=$((s + 5))
  [ "$s" -lt 300 ] || { ready=no; break; }
  [ $((s % 15)) -ne 0 ] || printf '  still starting (%d:%02d)...\n' $((s / 60)) $((s % 60))
done

if [ "$ready" = yes ]; then
  say ""
  say "OmicsBase is running: $APP"
else
  cat <<EOF

OmicsBase is installed and still starting.

  It has not answered after 5 minutes, which can happen on the first start or on an ARM
  machine. It keeps starting in the background: open $APP in a minute or two.
  To see how it is doing:
    cd $DIR && docker compose ps
    cd $DIR && docker compose logs api
EOF
fi

cat <<EOF

  Create an account there; the first one becomes the admin.
  Bring a model: paste a key (OpenRouter, OpenAI, Anthropic or Google) when you pick a
  model, or start a model in Ollama on this machine.

  Stop:    cd $DIR && docker compose down
  Update:  run the install command again
EOF
