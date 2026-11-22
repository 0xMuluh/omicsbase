#!/usr/bin/env bash
# Render the docs and copy them to the VM, where Caddy serves /docs/ from /srv/omicsbase/docs.
#
#   OMICSBASE_VM=user@host scripts/publish_docs.sh             current docs at /docs/
#   OMICSBASE_VM=user@host scripts/publish_docs.sh 2026.11.22  also keeps a copy at /docs/2026.11.22/
#
# Needs Quarto 1.11 or later (for llms.txt), gh (for the changelog), rsync and ssh to the VM.
# OMICSBASE_SSH_KEY selects a key file.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
vm=${OMICSBASE_VM:?set OMICSBASE_VM to user@host}
version=${1:-}
dest=/srv/omicsbase/docs
ssh_cmd=(ssh)
[ -z "${OMICSBASE_SSH_KEY:-}" ] || ssh_cmd+=(-i "$OMICSBASE_SSH_KEY")

"$root/scripts/build_changelog.sh"
quarto render "$root/docs" --profile publish
test -f "$root/docs/_site/llms.txt" || { echo "llms.txt is missing: render with Quarto 1.11 or later." >&2; exit 1; }

"${ssh_cmd[@]}" "$vm" "sudo install -d -o \$(id -un) -g \$(id -gn) $dest"
# The current docs replace the top level; release copies (folders named 2026.*) are kept.
rsync -a --delete --filter='P /20[0-9][0-9].*/' -e "${ssh_cmd[*]}" "$root/docs/_site/" "$vm:$dest/"
if [ -n "$version" ]; then
  # A release copy: same pages, without the files that tell search engines what to index.
  rsync -a --delete --exclude sitemap.xml --exclude robots.txt --exclude llms.txt \
    -e "${ssh_cmd[*]}" "$root/docs/_site/" "$vm:$dest/$version/"
fi
echo "Published: https://omicsbase.utu.fi/docs/${version:+ and /docs/$version/}"
