#!/usr/bin/env python3
"""Build the pinned/customized LibreChat image from the submodule source tree."""

import argparse
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
    parser.add_argument('--verify-only', action='store_true', help='Verify source without invoking Docker')
    args = parser.parse_args()

    source = ROOT / 'librechat'
    dockerfile = source / 'Dockerfile'

    if not dockerfile.exists():
        raise SystemExit(
            'librechat/Dockerfile not found. '
            'Run git submodule update --init --recursive first.'
        )

    commit = subprocess.check_output(
        ['git', '-C', str(source), 'rev-parse', 'HEAD'],
        text=True,
    ).strip()
    tag = args.tag or f'omicsbase-librechat:{commit[:9]}'

    status = subprocess.check_output(
        ['git', '-C', str(source), 'status', '--porcelain'],
        text=True,
    ).strip()
    branch = subprocess.check_output(
        ['git', '-C', str(source), 'rev-parse', '--abbrev-ref', 'HEAD'],
        text=True,
    ).strip()

    print(f'Verified LibreChat submodule at commit {commit[:9]} (branch: {branch})')
    if status:
        print(f'Warning: working tree has uncommitted modifications:\n{status}')
    else:
        print('Working tree is clean.')

    if args.verify_only:
        return

    subprocess.run([
        'docker', '--context', args.context, 'build', '--progress=plain',
        '--build-arg', f'BUILD_COMMIT={commit}', '-t', tag, str(source),
    ], check=True)

    print(f'Built {tag}; running containers were not changed.')


if __name__ == '__main__':
    main()

