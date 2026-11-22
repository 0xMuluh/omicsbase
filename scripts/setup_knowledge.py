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
        git(stage, 'fetch', '--depth=1', '--no-tags', book.get('fetch_repository') or book['repository'], book['commit'])
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


def book_branch(book, manifest):
    """The branch a book follows; 'release' stands for the manifest's release_branch."""
    branch = book.get('branch')
    return manifest.get('release_branch') if branch == 'release' else branch


def update_pins(manifest, resolve=None):
    """Move each book's pin to the head of its branch. Asks the remotes for the commit ids only;
    the sources are fetched by the next ordinary run. Returns {slug: (old, new)} for moved pins."""
    def ls_remote(repository, branch):
        result = subprocess.run(['git', 'ls-remote', repository, f'refs/heads/{branch}'], capture_output=True, text=True)
        if result.returncode or not result.stdout.strip():
            raise RuntimeError(f'Cannot read branch {branch} of {repository}: {result.stderr.strip()}')
        return result.stdout.split()[0]

    resolve = resolve or ls_remote
    moved = {}
    for index, book in enumerate(manifest['books']):
        branch = book_branch(book, manifest)
        if not branch:
            continue
        old = book['commit']
        new = resolve(book.get('fetch_repository') or book['repository'], branch)
        if new != old:
            # The license statement links carry the commit too.
            manifest['books'][index] = json.loads(json.dumps(book).replace(old, new))
            moved[book['slug']] = (old, new)
    return moved


def description_version(path):
    """The Version field of a source's DESCRIPTION file, if it has one."""
    try:
        for line in (Path(path) / 'DESCRIPTION').read_text(errors='ignore').splitlines():
            if line.startswith('Version:'):
                return line.split(':', 1)[1].strip()
    except OSError:
        pass
    return None


def install(manifest, cache, output, check_anchors=False):
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
            stats = build_index(layout, temporary, strict=True, books=manifest['books'])
            expected = {book['slug'] for book in manifest['books']}
            if set(stats) != expected or any(count <= 0 for count in stats.values()):
                raise RuntimeError(f'Incomplete knowledge coverage: {stats}')
            with sqlite3.connect(temporary) as conn:
                if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('SQLite integrity check failed')
                conn.execute('CREATE TABLE knowledge_sources (book_slug TEXT PRIMARY KEY, metadata_json TEXT NOT NULL)')
                for book in manifest['books']:
                    version = book.get('version') or description_version(downloaded[book['slug']])
                    record = dict(book, version=version) if version else book
                    conn.execute('INSERT INTO knowledge_sources VALUES (?, ?)', (book['slug'], json.dumps(record, sort_keys=True)))
                conn.execute('CREATE TABLE knowledge_build (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
                conn.execute('INSERT INTO knowledge_build VALUES (?, ?)', ('manifest_sha256', hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()))
            if check_anchors:
                from engine.knowledge.book_urls import check_anchors as check
                print(f'Checked section links against the live pages: {check(temporary, manifest["books"])}', flush=True)
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
    parser.add_argument('--check-anchors', action='store_true',
                        help='Fetch each book page once and keep only section links that exist on it')
    parser.add_argument('--update-pins', action='store_true',
                        help="Move the pins in knowledge/sources.json to each book's branch head, then stop")
    args = parser.parse_args()
    manifest_path = ROOT / 'knowledge/sources.json'
    manifest = json.loads(manifest_path.read_text())
    if args.update_pins:
        moved = update_pins(manifest)
        if moved:
            manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
        for slug, (old, new) in moved.items():
            print(f'{slug}: {old[:12]} -> {new[:12]}')
        print(f'{len(moved)} pins moved. Review knowledge/sources.json, then run again without --update-pins.')
        return
    try:
        install(manifest, args.cache_dir.resolve(), args.output.resolve(), check_anchors=args.check_anchors)
    except ImportError as error:
        raise SystemExit('Install knowledge dependencies first: python3 -m pip install -r knowledge/requirements.txt') from error


if __name__ == '__main__':
    main()
