"""Persistent per-user workspace revocation, independent of browser cookie domains."""
import hashlib
import json
import os
import secrets
import time
from pathlib import Path


def _path(user_id):
    root = Path(os.environ.get('OMICSBASE_STATE_ROOT', '/.openhands/omicsbase')) / 'sessions'
    root.mkdir(parents=True, exist_ok=True)
    return root / (hashlib.sha256(user_id.encode()).hexdigest() + '.json')


def state(user_id):
    path = _path(user_id)
    return json.loads(path.read_text()) if path.exists() else {'generation': 'initial', 'revoked_at': 0}


def revoke(user_id):
    path = _path(user_id)
    value = {'generation': secrets.token_hex(16), 'revoked_at': time.time()}
    temp = path.with_suffix('.' + secrets.token_hex(8) + '.tmp')
    temp.write_text(json.dumps(value))
    temp.replace(path)
