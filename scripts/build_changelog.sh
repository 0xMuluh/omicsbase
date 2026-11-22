#!/usr/bin/env bash
# Write docs/changelog.qmd from the GitHub releases whose tag is a version (2026.11.22), newest first.
set -euo pipefail
repo=${OMICSBASE_REPO:-0xMuluh/omicsbase}
out=${1:-"$(cd "$(dirname "$0")/.." && pwd)/docs/changelog.qmd"}
tags=$(gh release list -R "$repo" --limit 200 --json tagName --jq '.[].tagName | select(test("^20[0-9]{2}\\."))')
{
  printf -- '---\ntitle: "Changelog"\n---\n\n'
  printf 'Each version below is a [release on GitHub](https://github.com/%s/releases); its docs stay at `/docs/<version>/`.\n\n' "$repo"
  for tag in $tags; do
    gh release view "$tag" -R "$repo" --json tagName,publishedAt,body \
      --jq '"## \(.tagName) {#v\(.tagName | gsub("\\."; "-"))}\n\nReleased \(.publishedAt[:10]) · [docs for this version](https://omicsbase.utu.fi/docs/\(.tagName)/)\n\n\(.body)\n"' \
      | sed 's/^\(##*\) \(.*\)$/\1# \2/; s/^### \(20[0-9][0-9]\.\)/## \1/'  # release headings one level down
  done
} > "$out"
echo "$(printf '%s\n' $tags | grep -c . || true) releases written to $out"
