import base64
import hashlib
import hmac
import json
import os
import re
import time
from pathlib import Path
from omicsbase_shared.sessions import state

COOKIE = 'omicsbase_openhands'
AUDIENCE = 'omicsbase-openhands'


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', value):
        raise ValueError('Invalid identity')
    return value


def _encode_part(value):
    raw = json.dumps(value, separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')



def workspace_session_ttl():
    raw = os.environ.get('OMICSBASE_WORKSPACE_SESSION_TTL', '43200')
    try:
        ttl = int(raw)
    except (TypeError, ValueError):
        ttl = 43200

    # Keep accidental configuration within sensible bounds:
    # minimum 5 minutes, maximum 7 days.
    return max(300, min(ttl, 604800))


def issue_session_ticket(user_id, expires_in=3600):
    now = int(time.time())
    header = _encode_part({
        'alg': 'HS256',
        'typ': 'JWT',
    })
    payload = _encode_part({
        'sub': identifier(user_id),
        'purpose': 'session',
        'generation': state(user_id)['generation'],
        'aud': AUDIENCE,
        'iss': 'librechat',
        'iat': now,
        'exp': now + expires_in,
    })

    secret = os.environ['OMICSBASE_AUTH_SECRET'].encode()
    signature = hmac.new(
        secret,
        f'{header}.{payload}'.encode(),
        hashlib.sha256,
    ).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).decode().rstrip('=')

    return f'{header}.{payload}.{encoded_signature}'


def verify_ticket(token):
    header, payload, signature = token.split('.')
    secret = os.environ['OMICSBASE_AUTH_SECRET'].encode()
    expected = hmac.new(secret, f'{header}.{payload}'.encode(), hashlib.sha256).digest()
    actual = base64.urlsafe_b64decode(signature + '=' * (-len(signature) % 4))
    if not hmac.compare_digest(expected, actual):
        raise ValueError('Invalid signature')
    decode = lambda part: json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
    header_data = decode(header)
    if not isinstance(header_data, dict) or header_data.get('alg') != 'HS256':
        raise ValueError('Invalid algorithm')
    claims = decode(payload)
    if not isinstance(claims, dict):
        raise ValueError('Invalid claims')
    if claims.get('aud') != AUDIENCE or claims.get('iss') != 'librechat':
        raise ValueError('Invalid audience or issuer')
    if not isinstance(claims.get('exp'), (int, float)) or claims['exp'] <= time.time():
        raise ValueError('Expired ticket')
    identifier(claims.get('sub'))
    current = state(claims['sub'])
    if claims.get('purpose') == 'session' and claims.get('generation') != current['generation']:
        raise ValueError('Revoked session')
    if claims.get('purpose') == 'launch' and claims.get('iat', 0) < current['revoked_at']:
        raise ValueError('Revoked launch ticket')
    return claims


def runtime_name(user_id):
    return 'openhands-user-' + hashlib.sha256(identifier(user_id).encode()).hexdigest()[:32]


def project_root():
    return Path(os.environ.get('OMICSBASE_PROJECTS_ROOT', '/opt/workspace_base'))


def user_root(user_id):
    return project_root() / 'users' / identifier(user_id)
