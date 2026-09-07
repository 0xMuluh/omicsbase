#!/usr/bin/env python3
"""Apply the pinned source customization, test/build it, and package OpenHands.

No minified assets are rewritten at runtime. Source patch conflicts, type errors,
and contract tests stop the build before an image is produced.
"""
import argparse
import hashlib
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
    parser.add_argument('--prepared-source', type=Path, help='Reuse a locally validated source checkout/build; source delta must match exactly')
    args = parser.parse_args()
    config = json.loads((ROOT / 'integrations/openhands/frontend.json').read_text())
    patch = ROOT / 'integrations/openhands/frontend.patch'
    if hashlib.sha256(patch.read_bytes()).hexdigest() != config['patch_sha256']:
        raise SystemExit('Frontend patch checksum does not match its manifest.')
    with tempfile.TemporaryDirectory(prefix='omicsbase-openhands-') as temp:
        temp = Path(temp)
        source = args.prepared_source.resolve() if args.prepared_source else temp / 'source'
        if not args.prepared_source:
            source.mkdir()
            run(['git', 'init', '-q'], source)
            run(['git', 'fetch', '--depth=1', config['repository'], config['commit']], source)
            run(['git', 'checkout', '--detach', 'FETCH_HEAD'], source)
            run(['git', 'apply', '--check', str(patch)], source)
            run(['git', 'apply', str(patch)], source)
        else:
            head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
            delta = subprocess.check_output(['git', 'diff', 'HEAD', '--binary', '--full-index', '--', 'frontend'], cwd=source)
            if head != config['commit'] or delta != patch.read_bytes():
                raise SystemExit('Prepared source does not match the pinned customization.')
        frontend = source / 'frontend'
        if not args.prepared_source:
            run(['npm', 'ci', '--no-audit', '--no-fund'], frontend)
        run(['npm', 'run', 'typecheck'], frontend)
        run(['npm', 'test', '--', '__tests__/integrations/omicsbase.test.ts'], frontend)
        run(['npm', 'run', 'build'], frontend)
        context = temp / 'image'
        shutil.copytree(frontend / 'build', context / 'frontend')
        (context / 'frontend/omicsbase-build.json').write_text(json.dumps(config) + '\n')
        integration = ROOT / 'upstream/librechat/additions/patches/omicsbase_shared'
        # The checked-in snapshot is the source of truth for distributable builds.
        shutil.copytree(integration, context / 'integration', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        shutil.copyfile(ROOT / 'docker/openhands/Dockerfile', context / 'Dockerfile')
        run(['docker', '--context', args.context, 'build', '-t', args.tag, str(context)])
    print(f'Built {args.tag}; running containers were not changed.')


if __name__ == '__main__':
    main()
