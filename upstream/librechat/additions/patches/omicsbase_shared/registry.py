import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from omicsbase_shared.identity import identifier, project_root, user_root


def state_root():
    root = Path(os.environ.get('OMICSBASE_STATE_ROOT', '/.openhands/omicsbase'))
    root.mkdir(parents=True, exist_ok=True)
    return root


@contextmanager
def lock(name):
    with (state_root() / f'{identifier(name)}.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def bind_project(user_id, project_id, conversation_id):
    user_id, project_id, conversation_id = map(identifier, (user_id, project_id, conversation_id))
    with lock('project-' + project_id):
        root = user_root(user_id)
        root.mkdir(parents=True, exist_ok=True)
        destination = root / project_id
        legacy = project_root() / project_id
        if legacy.is_symlink():
            if legacy.resolve() != destination.resolve():
                raise ValueError('Project belongs to a different user')
        elif legacy.exists():
            if destination.exists():
                raise ValueError('Both legacy and user project directories exist; refusing to overwrite')
            legacy.rename(destination)
        destination.mkdir(exist_ok=True)
        if not legacy.exists():
            legacy.symlink_to(Path('users') / user_id / project_id, target_is_directory=True)
        path = state_root() / f'{conversation_id}.json'
        with path.open('x') as handle:
            json.dump({'user_id': user_id, 'project_id': project_id}, handle)
    return f'/workspace/{project_id}'


def conversation_project(user_id, conversation_id):
    path = state_root() / f'{identifier(conversation_id)}.json'
    if not path.exists():
        return '/workspace'
    entry = json.loads(path.read_text())
    if entry['user_id'] != identifier(user_id):
        raise ValueError('Conversation owner mismatch')
    return '/workspace/' + identifier(entry['project_id'])
