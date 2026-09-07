import base64
import hashlib
import hmac
import json
import os
import re
import time
from pathlib import Path

COOKIE = 'omicsbase_openhands'
AUDIENCE = 'omicsbase-openhands'


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', value):
        raise ValueError('Invalid identity')
    return value


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
    return claims


def runtime_name(user_id):
    return 'openhands-user-' + hashlib.sha256(identifier(user_id).encode()).hexdigest()[:32]


def project_root():
    return Path(os.environ.get('OMICSBASE_PROJECTS_ROOT', '/opt/workspace_base'))


def user_root(user_id):
    return project_root() / 'users' / identifier(user_id)
