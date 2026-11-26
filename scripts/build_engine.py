#!/usr/bin/env python3
"""Build the engine image from engine/Dockerfile using only this repository's sources."""
import argparse
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', default='default', help='Docker context (default: default)')
    parser.add_argument('--tag', default='omicsbase-engine:dev')
    args = parser.parse_args()
    subprocess.run(['docker', '--context', args.context, 'build', '--progress=plain',
                    '-t', args.tag, str(ROOT / 'engine')], check=True)
    print(f'Built {args.tag}. Running containers and deployment configuration were not changed.')


if __name__ == '__main__':
    main()
