"""Verify a checkout against the distributed snapshot and stage source-only builds."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile


def git(source, *args):
    return subprocess.check_output(['git', '-C', str(source), *args])


def stage_source(root: Path, name: str, destination: Path) -> dict:
    source = root / name
    snapshot = root / 'upstream' / name
    entry = json.loads((root / 'upstream/manifest.json').read_text())[name]
    if git(source, 'rev-parse', 'HEAD').decode().strip() != entry['commit']:
        raise ValueError(f'{name}: HEAD differs from the manifest baseline')
    patch = (snapshot / 'changes.patch').read_bytes()
    if hashlib.sha256(patch).hexdigest() != entry['patch_sha256']:
        raise ValueError(f'{name}: snapshot patch checksum mismatch')
    actual = git(source, 'diff', '--binary', '--full-index', '--no-ext-diff', 'HEAD', '--')
    if actual != patch:
        raise ValueError(f'{name}: tracked edits differ from snapshot; review and snapshot first')
    additions = entry['addition_sha256']
    extra = set(filter(None, git(source, 'ls-files', '--others', '--exclude-standard', '-z').decode().split('\0'))) - set(additions)
    extra = {p for p in extra if '__pycache__' not in Path(p).parts and not p.endswith('.pyc')}
    if extra:
        raise ValueError(f'{name}: source additions absent from snapshot: {sorted(extra)}')
    for path, checksum in additions.items():
        p = Path(path)
        if p.is_absolute() or '..' in p.parts:
            raise ValueError('Invalid snapshot path')
        for file in [snapshot / 'additions' / p, source / p]:
            if file.is_symlink() or not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != checksum:
                raise ValueError(f'{name}: addition differs from snapshot: {path}')
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile() as archive:
        subprocess.run(['git', '-C', str(source), 'archive', entry['commit']], stdout=archive, check=True)
        archive.seek(0)
        with tarfile.open(fileobj=archive) as tar:
            tar.extractall(destination, filter='data')
    if patch:
        subprocess.run(['git', '-C', str(destination), 'apply', str((snapshot / 'changes.patch').resolve())], check=True)
    for path in additions:
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snapshot / 'additions' / path, target)
    # Compare the staged tree as well, detecting assume-unchanged/skip-worktree edits.
    for file in destination.rglob('*'):
        if file.is_file():
            actual_file = source / file.relative_to(destination)
            if not actual_file.is_file() or file.read_bytes() != actual_file.read_bytes():
                raise ValueError(f'{name}: working file differs: {file.relative_to(destination)}')
    return entry
