#!/usr/bin/env python3
"""Build the analysis base and engine using only this repository's sources."""
import argparse
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', default='default', help='Docker context (default: default)')
    parser.add_argument('--base-tag', default='omicsbase-engine-base:dev')
    parser.add_argument('--tag', default='omicsbase-engine:dev')
    args = parser.parse_args()
    docker = ['docker', '--context', args.context, 'build', '--progress=plain']
    base = ROOT / 'docker/engine-base'
    subprocess.run(docker + ['-f', str(base / 'backend/Dockerfile'), '-t', args.base_tag, str(base)], check=True)
    subprocess.run(docker + ['--build-arg', f'ENGINE_BASE_IMAGE={args.base_tag}', '-t', args.tag, str(ROOT / 'engine')], check=True)
    print(f'Built {args.tag}. Running containers and deployment configuration were not changed.')


if __name__ == '__main__':
    main()
