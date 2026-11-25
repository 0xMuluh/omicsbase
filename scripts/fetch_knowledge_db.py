#!/usr/bin/env python3
"""Copy engine/knowledge/knowledge.db out of a published engine image.

Downloads only the image's top layers, newest first, until one holds the file, so the
image itself is never pulled. For CI and for builds without scripts/setup_knowledge.py.

    python3 scripts/fetch_knowledge_db.py ghcr.io/0xmuluh/omicsbase-engine:2026.11.25
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MEMBER = 'app/engine/knowledge/knowledge.db'
ACCEPT = ', '.join([
    'application/vnd.oci.image.index.v1+json',
    'application/vnd.oci.image.manifest.v1+json',
    'application/vnd.docker.distribution.manifest.list.v2+json',
    'application/vnd.docker.distribution.manifest.v2+json',
])


class Registry:
    def __init__(self, registry, repository):
        self.base = f'https://{registry}/v2/{repository}'
        self.repository = repository
        self.token = None

    def _open(self, path, accept=None):
        headers = {'Accept': accept} if accept else {}
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'
        try:
            return urllib.request.urlopen(urllib.request.Request(self.base + path, headers=headers))
        except urllib.error.HTTPError as error:
            if error.code != 401 or self.token:
                raise
            self._login(error.headers.get('WWW-Authenticate', ''))
            return self._open(path, accept)

    def _login(self, challenge):
        # Anonymous pull token: Bearer realm="...",service="...",scope="..."
        fields = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
        query = urllib.parse.urlencode({'service': fields.get('service', ''),
                                        'scope': fields.get('scope') or f'repository:{self.repository}:pull'})
        with urllib.request.urlopen(f"{fields['realm']}?{query}") as response:
            body = json.load(response)
        self.token = body.get('token') or body['access_token']

    def manifest(self, reference):
        with self._open(f'/manifests/{reference}', ACCEPT) as response:
            manifest = json.load(response)
        for entry in manifest.get('manifests', []):
            platform = entry.get('platform', {})
            if (platform.get('os'), platform.get('architecture')) == ('linux', 'amd64'):
                return self.manifest(entry['digest'])
        if 'layers' not in manifest:
            raise SystemExit(f'No linux/amd64 image under {reference}.')
        return manifest

    def blob(self, digest):
        return self._open(f'/blobs/{digest}')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('image', help='registry/repository:tag, e.g. ghcr.io/0xmuluh/omicsbase-engine:2026.11.25')
    parser.add_argument('--output', type=Path, default=ROOT / 'engine/knowledge/knowledge.db')
    args = parser.parse_args()

    name, _, tag = args.image.rpartition(':')
    if not name or '/' in tag:
        raise SystemExit('Give the image with its tag, e.g. ghcr.io/0xmuluh/omicsbase-engine:2026.11.25')
    registry, _, repository = name.partition('/')
    source = Registry(registry, repository)

    for layer in reversed(source.manifest(tag)['layers']):
        with source.blob(layer['digest']) as response, tarfile.open(fileobj=response, mode='r|*') as archive:
            for member in archive:
                path = member.name.lstrip('./')
                if path == 'app/engine/knowledge/.wh.knowledge.db':
                    raise SystemExit(f'{args.image} removes knowledge.db.')
                if path != MEMBER or not member.isfile():
                    continue
                args.output.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=args.output.parent, delete=False) as handle:
                    shutil.copyfileobj(archive.extractfile(member), handle)
                Path(handle.name).replace(args.output)
                print(f'Wrote {args.output} ({member.size} bytes) from {args.image}, layer {layer["digest"][:19]}.')
                return
    raise SystemExit(f'{args.image} has no /{MEMBER}.')


if __name__ == '__main__':
    main()
