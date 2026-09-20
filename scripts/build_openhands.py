#!/usr/bin/env python3
"""Build and package OpenHands from the submodule or validated source tree."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def run(args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', default='default')
    parser.add_argument('--tag', default='omicsbase-openhands:dev')
    parser.add_argument('--prepared-source', type=Path, help='Reuse a locally validated source checkout/build')
    parser.add_argument('--verify-only', action='store_true', help='Verify source and test contracts without building image')
    parser.add_argument('--skip-build', action='store_true', help='Skip npm run build if already built in submodule')
    args = parser.parse_args()

    submodule = ROOT / 'openhands'
    if args.prepared_source:
        source = args.prepared_source.resolve()
    elif (submodule / 'package.json').exists():
        source = submodule
    else:
        raise SystemExit('openhands submodule not found. Run git submodule update --init --recursive first.')

    commit = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    branch = subprocess.check_output(['git', '-C', str(source), 'rev-parse', '--abbrev-ref', 'HEAD'], text=True).strip()
    status = subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain'], text=True).strip()
    print(f'Verified OpenHands source at commit {commit[:9]} (branch: {branch})')
    if status:
        print(f'Warning: working tree has uncommitted modifications:\n{status}')
    else:
        print('Working tree is clean.')

    is_monorepo = (source / 'frontend').exists()
    frontend = source / 'frontend' if is_monorepo else source

    run(['npx', 'vitest', 'run', '--environment', 'node', '__tests__/integrations/omicsbase.test.ts'], frontend)

    if args.verify_only:
        print('Verification complete (vitest contract tests passed).')
        return

    if not args.skip_build or not (frontend / 'build').exists():
        run(['npm', 'run', 'make-i18n'], frontend)
        run(['npm', 'run', 'typecheck'], frontend)
        run(['npm', 'run', 'build'], frontend)

    config = {
        'repository': 'https://github.com/0xMuluh/OpenHands.git',
        'commit': commit,
        'branch': branch,
        'contract': 1,
    }

    with tempfile.TemporaryDirectory(prefix='omicsbase-openhands-') as temp:
        temp = Path(temp)
        context = temp / 'image'
        shutil.copytree(frontend / 'build', context / 'frontend')
        (context / 'frontend/omicsbase-build.json').write_text(json.dumps(config) + '\n')
        gateway_src = ROOT / 'docker/openhands/gateway'
        shutil.copytree(gateway_src, context / 'gateway', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        shutil.copyfile(ROOT / 'docker/openhands/entrypoint.sh', context / 'entrypoint.sh')
        shutil.copyfile(ROOT / 'docker/openhands/Dockerfile', context / 'Dockerfile')
        run(['docker', '--context', args.context, 'build', '-t', args.tag, str(context)])

    print(f'Built {args.tag}; running containers were not changed.')


if __name__ == '__main__':
    main()

