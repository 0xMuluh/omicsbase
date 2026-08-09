#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATE_TAG="$(date +%F)"
OUT_DIR="${ROOT_DIR}/dist/report-bundles"
DRY_RUN="false"

usage() {
  cat <<'USAGE'
Usage:
  scripts/package_analysis_reports.sh [--date YYYY-MM-DD] [--output-dir PATH] [--dry-run]

Options:
  --date        Version tag used in archive names (default: today, YYYY-MM-DD).
  --output-dir  Destination directory for ZIP bundles (default: dist/report-bundles).
  --dry-run     Validate inputs and print planned bundles without creating ZIP files.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --date)
      DATE_TAG="${2:-}"
      shift 2
      ;;
    --output-dir)
      OUT_DIR="${2:-}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ ! "$DATE_TAG" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "Error: --date must be in YYYY-MM-DD format." >&2
  exit 1
fi

if [[ "$DRY_RUN" == "false" ]] && ! command -v zip >/dev/null 2>&1; then
  echo "Error: zip command not found. Install zip and retry." >&2
  exit 1
fi

package_one() {
  local analysis_key="$1"
  local source_dir="$2"
  local destination_zip="${OUT_DIR}/${analysis_key}__${DATE_TAG}.zip"

  if [[ ! -d "$source_dir" ]]; then
    echo "Error: source directory not found: $source_dir" >&2
    return 1
  fi

  if [[ ! -f "$source_dir/index.html" ]]; then
    echo "Error: missing index.html at root of $source_dir" >&2
    return 1
  fi

  if [[ "$DRY_RUN" == "true" ]]; then
    echo "[dry-run] would create: $destination_zip"
    return 0
  fi

  mkdir -p "$OUT_DIR"
  rm -f "$destination_zip"

  (
    cd "$source_dir"
    zip -qr "$destination_zip" .
  )

  echo "created: $destination_zip"
}

package_one "prenatal_diet" "${ROOT_DIR}/output"
package_one "child_diet" "${ROOT_DIR}/output"

if [[ "$DRY_RUN" == "false" ]]; then
  echo "done: bundles are in ${OUT_DIR}"
fi
