import json
from pathlib import Path
from fastapi import HTTPException
from omicsbase_shared.registry import state_root
from omicsbase_shared.identity import identifier, user_root, project_root

def _get_project_dir(conversation_id: str) -> Path:
    try:
        cid = identifier(conversation_id)
        path = state_root() / f'{cid}.json'
        if path.exists():
            entry = json.loads(path.read_text())
            user_id = entry.get('user_id')
            project_id = entry.get('project_id')
            if user_id and project_id:
                pdir = user_root(user_id) / project_id
                if pdir.is_dir():
                    return pdir
        default_path = project_root() / cid
        if default_path.is_dir():
            return default_path
    except Exception:
        pass
    return project_root()


def _resolve_safe_path(base_dir: Path, rel_path: str) -> Path:
    clean_rel = rel_path.lstrip('/').replace('\\', '/')
    target = (base_dir / clean_rel).resolve()
    base_resolved = base_dir.resolve()
    if not target.is_relative_to(base_resolved):
        raise HTTPException(status_code=403, detail='Access denied: path traversal')
    return target


def _build_file_tree(base_dir: Path, current_dir: Path = None) -> list[dict]:
    if current_dir is None:
        current_dir = base_dir
    items = []
    try:
        entries = sorted(list(current_dir.iterdir()), key=lambda e: (not e.is_dir(), e.name.lower()))
    except Exception:
        return items

    for entry in entries:
        if entry.name in ('.git', '__pycache__', '.pytest_cache', '.quarto', '_site'):
            continue
        rel_path = str(entry.relative_to(base_dir))
        if entry.is_dir():
            items.append({
                'name': entry.name,
                'path': rel_path,
                'type': 'directory',
                'children': _build_file_tree(base_dir, entry)
            })
        else:
            try:
                size = entry.stat().st_size
            except Exception:
                size = 0
            items.append({
                'name': entry.name,
                'path': rel_path,
                'type': 'file',
                'size': size
            })
    return items

