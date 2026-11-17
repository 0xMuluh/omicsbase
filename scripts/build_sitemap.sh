#!/usr/bin/env bash
# Write sitemap.xml listing the front page and every publicly shared example note.
# Run on the VM: scripts/build_sitemap.sh [output] (default /srv/omicsbase/seo/sitemap.xml)
set -euo pipefail
out="${1:-/srv/omicsbase/seo/sitemap.xml}"
host="${OMICSBASE_PUBLIC_HOST:-https://omicsbase.utu.fi}"
tmp="$(mktemp)"
docker exec chat-mongodb mongosh LibreChat --quiet --eval "
const ids = db.aclentries.distinct('resourceId', {resourceType: 'sharedLink', principalType: 'public'});
print('<?xml version=\"1.0\" encoding=\"UTF-8\"?>');
print('<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">');
print('  <url><loc>${host}/</loc></url>');
db.sharedlinks.find({_id: {\$in: ids}}, {shareId: 1, updatedAt: 1}).sort({createdAt: 1}).forEach(s =>
  print('  <url><loc>${host}/share/' + s.shareId + '</loc><lastmod>' + s.updatedAt.toISOString().slice(0, 10) + '</lastmod></url>'));
print('</urlset>');
" > "$tmp"
chmod 644 "$tmp"
mv "$tmp" "$out"
echo "$(grep -c '/share/' "$out") examples written to $out"
