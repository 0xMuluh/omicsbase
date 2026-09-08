#!/usr/bin/env python3
"""Build the pinned/customized LibreChat image from the restored source tree."""

import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--context',
        default='default',
        help='Docker context (default: default)',
    )
    parser.add_argument(
        '--tag',
        help='Override image tag; default is derived from pinned LibreChat commit',
    )
    args = parser.parse_args()

    manifest = json.loads((ROOT / 'upstream/manifest.json').read_text())
    commit = manifest['librechat']['commit']
    tag = args.tag or f'omicsbase-librechat:{commit[:9]}'

    source = ROOT / 'librechat'
    dockerfile = source / 'Dockerfile'

    if not dockerfile.exists():
        raise SystemExit(
            'librechat/Dockerfile not found. '
            'Run scripts/restore_upstream.py --in-place first.'
        )

    cmd = [
        'docker',
        '--context',
        args.context,
        'build',
        '--progress=plain',
        '-t',
        tag,
        str(source),
    ]

    print(f'Building LibreChat pinned at {commit}')
    print(f'Image: {tag}')
    subprocess.run(cmd, check=True)
    print(f'Built {tag}; running containers were not changed.')


if __name__ == '__main__':
    main()
