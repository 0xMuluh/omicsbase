#!/usr/bin/env python3
"""Restore pinned source checkouts into a NEW directory; does not start services."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', nargs='?', type=Path, help='New directory for both checkouts')
    parser.add_argument('--in-place', action='store_true', help='Restore into this repository; both checkout paths must be absent')
    args = parser.parse_args()
    if args.in_place:
        if args.destination is not None:
            parser.error('Do not combine --in-place with a destination.')
        dest = ROOT
        if any((dest / name).exists() for name in ('librechat', 'openhands')):
            parser.error('Existing upstream checkout found; refusing to overwrite it.')
    else:
        if args.destination is None:
            parser.error('Provide a new destination or --in-place.')
        dest = args.destination.resolve()
        if dest.exists():
            parser.error('Destination must not exist; existing checkouts are never overwritten.')
    manifest = json.loads((ROOT / 'upstream/manifest.json').read_text())
    # Validate the entire snapshot before cloning or applying anything.
    for name, entry in manifest.items():
        source = ROOT / 'upstream' / name
        checks = {'changes.patch': entry['patch_sha256']}
        checks.update({'additions/' + p: sha for p, sha in entry['addition_sha256'].items()})
        for relative, sha in checks.items():
            if hashlib.sha256((source / relative).read_bytes()).hexdigest() != sha:
                raise ValueError(f'Snapshot checksum mismatch: {name}/{relative}')
    dest.mkdir(parents=True, exist_ok=args.in_place)
    for name, entry in manifest.items():
        repo = dest / name
        subprocess.run(['git', 'clone', '--no-checkout', entry['url'], str(repo)], check=True)
        subprocess.run(['git', '-C', str(repo), 'checkout', '--detach', entry['commit']], check=True)
        patch = ROOT / 'upstream' / name / 'changes.patch'
        if patch.stat().st_size:
            subprocess.run(['git', '-C', str(repo), 'apply', '--check', str(patch)], check=True)
            subprocess.run(['git', '-C', str(repo), 'apply', str(patch)], check=True)
        additions = ROOT / 'upstream' / name / 'additions'
        if additions.exists():
            shutil.copytree(additions, repo, dirs_exist_ok=True)
    print(f'Restored source in {dest}. Dependencies, secrets, data, and built images are separate.')


if __name__ == '__main__':
    main()
