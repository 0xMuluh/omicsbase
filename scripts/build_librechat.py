#!/usr/bin/env python3
"""Build the pinned/customized LibreChat image from the restored source tree."""

import argparse
import json
from pathlib import Path
import subprocess
import tempfile
from verify_source import stage_source

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
    parser.add_argument('--verify-only', action='store_true', help='Verify source without invoking Docker')
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

    with tempfile.TemporaryDirectory(prefix='omicsbase-librechat-') as directory:
        context = Path(directory) / 'source'
        stage_source(ROOT, 'librechat', context)
        print(f'Verified LibreChat baseline {commit} and all snapshot customizations')
        if args.verify_only:
            return
        subprocess.run([
            'docker', '--context', args.context, 'build', '--progress=plain',
            '--build-arg', f'BUILD_COMMIT={commit}', '-t', tag, str(context),
        ], check=True)

    print(f'Built {tag}; running containers were not changed.')


if __name__ == '__main__':
    main()
