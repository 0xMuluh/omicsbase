#!/usr/bin/env python3
"""Capture upstream deltas without modifying the development checkouts.

Run from any directory. Existing snapshots are replaced only after both checkouts
have been read successfully. Review the resulting Git diff before committing.
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args])


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    with tempfile.TemporaryDirectory(prefix='omicsbase-snapshot-') as directory:
        stage = Path(directory)
        manifest = {}
        for name in ('librechat', 'openhands'):
            repo = ROOT / name
            out = stage / name
            out.mkdir()
            base = git(repo, 'rev-parse', 'HEAD').decode().strip()
            patch = git(repo, 'diff', '--binary', '--full-index', '--no-ext-diff', 'HEAD', '--')
            (out / 'changes.patch').write_bytes(patch)
            additions = {}
            for item in git(repo, 'ls-files', '--others', '--exclude-standard', '-z').decode().split('\0'):
                if not item or '__pycache__' in Path(item).parts or item.endswith('.pyc'):
                    continue
                path = Path(item)
                if path.is_absolute() or '..' in path.parts or path.name.startswith('.env') or path.suffix in {'.pem', '.key', '.db', '.sqlite', '.zip'}:
                    raise ValueError(f'Review non-source file before capturing: {name}/{item}')
                source = repo / path
                if source.is_symlink() or source.stat().st_size > 2_000_000:
                    raise ValueError(f'Review symlink/large file before capturing: {name}/{item}')
                target = out / 'additions' / path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                additions[item] = digest(source.read_bytes())
            manifest[name] = {
                'url': git(repo, 'remote', 'get-url', 'origin').decode().strip(),
                'commit': base,
                'patch_sha256': digest(patch),
                'addition_sha256': additions,
            }
        snapshots = ROOT / 'upstream'
        snapshots.mkdir(exist_ok=True)
        for name in manifest:
            dest = snapshots / name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(stage / name, dest)
        (snapshots / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        print('Captured upstream patches and additions; review with git diff before committing.')


if __name__ == '__main__':
    main()
