#!/bin/bash
# Publish a release's Apptainer image to ghcr.io, so a project's first Reports launch
# downloads it instead of converting the Docker image (pull_image.sbatch).
#
# Run once per release on a Roihu login node, after building the .sif with pull_image.sbatch:
#   publish_sif.sh <path to .sif> <tag>        e.g. ... omicsbase-reports_v1.41.0-roihu1.sif v1.41.0-roihu1
#
# Asks for a GitHub token with write:packages. It is kept only in a private temporary file
# for the push and deleted afterwards.
set -euo pipefail

sif=$1
tag=$2
read -rp "GitHub user: " user
read -rsp "GitHub token (write:packages): " token
echo

dir=$(mktemp -d)
trap 'rm -rf "$dir"' EXIT
chmod 700 "$dir"
printf '{"auths":{"ghcr.io":{"auth":"%s"}}}' "$(printf '%s:%s' "$user" "$token" | base64 -w0)" > "$dir/auth.json"
unset token

apptainer push --authfile "$dir/auth.json" "$sif" "oras://ghcr.io/0xmuluh/omicsbase-reports:$tag-sif"
echo "Published oras://ghcr.io/0xmuluh/omicsbase-reports:$tag-sif"
