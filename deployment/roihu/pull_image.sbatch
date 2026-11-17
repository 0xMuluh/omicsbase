#!/bin/bash
# Pull a Reports image release from ghcr.io into an Apptainer image on Roihu.
#
#   sbatch --account=<project> pull_image.sbatch <tag> <images folder>
#
# Writes <images folder>/omicsbase-reports_<tag>.sif. Run once per release; every
# user's Reports job then starts from the same file.
#SBATCH --partition=small
#SBATCH --time=02:00:00
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --job-name=omicsbase-reports-pull
#SBATCH --output=%x-%j.out
set -euo pipefail

tag=$1
images=$2
sif=$images/omicsbase-reports_$tag.sif
work=$(mktemp -d "$images/.pull-$tag-XXXX")
# Unpacking the R library means many small files: slow on Lustre scratch, fast in the
# node's memory (/dev/shm, counted against --mem).
shm=$(mktemp -d /dev/shm/omicsbase-pull-XXXX)
trap 'rm -rf "$work" "$shm"' EXIT

export APPTAINER_CACHEDIR=$shm/cache
export APPTAINER_TMPDIR=$shm/tmp
mkdir -p "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR"

start=$(date +%s)
apptainer pull --force "$work/image.sif" "docker://ghcr.io/0xmuluh/omicsbase-reports:$tag"
mv "$work/image.sif" "$sif"
ls -la "$sif"
echo "seconds: $(( $(date +%s) - start ))"
