#!/bin/bash
# Turn a Reports image release from ghcr.io into the project's Apptainer image on Roihu.
#
# OmicsBase submits this through FirecREST before a project's first Reports session, with:
#   REPORTS_TAG       image tag, e.g. v1.41.0-roihu1
#   REPORTS_IMAGES    the project's image folder; writes omicsbase-reports_<tag>.sif there
# Project members share the image; a lock keeps two first launches from building it twice.
set -euo pipefail
umask 027

tag=$REPORTS_TAG
images=$REPORTS_IMAGES
sif=$images/omicsbase-reports_$tag.sif
mkdir -p "$images"
exec 9> "$images/.pull.lock"
flock 9
if [ -f "$sif" ]; then
  echo "$sif is already there."
  exit 0
fi

work=$(mktemp -d "$images/.pull-$tag-XXXX")
# Unpacking the R library means many small files: slow on Lustre scratch, fast in the
# node's memory (/dev/shm, counted against the job's memory).
shm=$(mktemp -d /dev/shm/omicsbase-pull-XXXX)
trap 'rm -rf "$work" "$shm"' EXIT
export APPTAINER_CACHEDIR=$shm/cache
export APPTAINER_TMPDIR=$shm/tmp
mkdir -p "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR"

# Fast path: the finished image, published with each release (publish_sif.sh). Otherwise
# convert the Docker image (about 7 minutes and 64 GB of memory).
pull() {
  apptainer pull --force "$work/image.sif" "oras://ghcr.io/0xmuluh/omicsbase-reports:$tag-sif" && return
  echo "No published image for $tag; converting the Docker image."
  apptainer pull --force "$work/image.sif" "docker://ghcr.io/0xmuluh/omicsbase-reports:$tag"
}

start=$(date +%s)
# ghcr.io sometimes drops a download; one more try covers a short network problem.
if ! pull; then
  echo "Downloading the image failed; trying once more in 60 s."
  sleep 60
  pull
fi
mv "$work/image.sif" "$sif"
echo "$sif built in $(( $(date +%s) - start )) s"
