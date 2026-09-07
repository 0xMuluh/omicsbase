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
    parser.add_argument('destination', type=Path, help='New, nonexistent directory for both checkouts')
    args = parser.parse_args()
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
    dest.mkdir(parents=True)
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
