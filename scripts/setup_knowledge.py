#!/usr/bin/env python3
"""Fetch pinned public book sources and atomically install the search index.

Downloads source; never renders books or executes their R/Python chunks.
The books retain their own licenses, documented in knowledge/ATTRIBUTION.md.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def git(repo, *args):
    result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f'Git failed for {repo.name}: {result.stderr.strip()}')
    return result.stdout.strip()


def fetch(book, sources):
    parent = sources / book['slug']
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / book['commit']
    if target.exists():
        if git(target, 'rev-parse', 'HEAD') != book['commit'] or git(target, 'status', '--porcelain', '--untracked-files=all'):
            raise RuntimeError(f'Cached source differs from its pin: {target}')
        # Detect sparse/deleted source checkouts rather than accepting incomplete content.
        if any(line.startswith(('S ', 's ')) for line in git(target, 'ls-files', '-v').splitlines()):
            raise RuntimeError(f'Sparse source checkout is unsupported: {target}')
        return book['slug'], target
    stage = Path(tempfile.mkdtemp(prefix='download-', dir=parent))
    try:
        git(stage, 'init', '-q')
        git(stage, 'fetch', '--depth=1', '--no-tags', book['repository'], book['commit'])
        git(stage, 'checkout', '--detach', 'FETCH_HEAD')
        if git(stage, 'rev-parse', 'HEAD') != book['commit']:
            raise RuntimeError(f'Commit verification failed: {book["slug"]}')
        # Nested submodules are not silently treated as downloaded book sources.
        if any(line.startswith('160000 ') for line in git(stage, 'ls-files', '--stage').splitlines()):
            raise RuntimeError(f'Source has submodules requiring explicit pins: {book["slug"]}')
        stage.rename(target)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    print(f'Fetched {book["slug"]} at {book["commit"][:12]}', flush=True)
    return book['slug'], target


def install(manifest, cache, output):
    from engine.knowledge.indexer import build_index
    from engine.knowledge.search import search_bioc_knowledge

    output.parent.mkdir(parents=True, exist_ok=True)
    sources = cache / 'sources'
    sources.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as executor:
        downloaded = dict(executor.map(lambda book: fetch(book, sources), manifest['books']))
    # The original indexer expects <root>/<slug>; only this temporary layout uses links.
    with tempfile.TemporaryDirectory(prefix='index-', dir=cache) as directory:
        layout = Path(directory)
        for slug, path in downloaded.items():
            (layout / slug).symlink_to(path.resolve(), target_is_directory=True)
        handle, temporary = tempfile.mkstemp(prefix='.knowledge-', suffix='.db', dir=output.parent)
        os.close(handle)
        temporary = Path(temporary)
        try:
            stats = build_index(layout, temporary, strict=True)
            expected = {book['slug'] for book in manifest['books']}
            if set(stats) != expected or any(count <= 0 for count in stats.values()):
                raise RuntimeError(f'Incomplete knowledge coverage: {stats}')
            with sqlite3.connect(temporary) as conn:
                if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('SQLite integrity check failed')
                conn.execute('CREATE TABLE knowledge_sources (book_slug TEXT PRIMARY KEY, metadata_json TEXT NOT NULL)')
                for book in manifest['books']:
                    conn.execute('INSERT INTO knowledge_sources VALUES (?, ?)', (book['slug'], json.dumps(book, sort_keys=True)))
                conn.execute('CREATE TABLE knowledge_build (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
                conn.execute('INSERT INTO knowledge_build VALUES (?, ?)', ('manifest_sha256', hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()))
            result = search_bioc_knowledge('analysis', db_path=temporary)
            if result['status'] != 'success' or result['count'] == 0:
                raise RuntimeError('Search smoke test failed')
            os.chmod(temporary, 0o644)
            os.replace(temporary, output)
        finally:
            if temporary.exists():
                temporary.unlink()
    print(f'Installed {sum(stats.values())} chunks from {len(stats)} books at {output}', flush=True)
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-dir', type=Path, default=ROOT / '.knowledge')
    parser.add_argument('--output', type=Path, default=ROOT / 'engine/knowledge/knowledge.db')
    args = parser.parse_args()
    manifest = json.loads((ROOT / 'knowledge/sources.json').read_text())
    try:
        install(manifest, args.cache_dir.resolve(), args.output.resolve())
    except ImportError as error:
        raise SystemExit('Install knowledge dependencies first: python3 -m pip install -r knowledge/requirements.txt') from error


if __name__ == '__main__':
    main()
