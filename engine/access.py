"""Project-scoped browser authorization; internal MCP remains on the private network."""
import base64
import hashlib
import hmac
import json
import os
import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


def verify_project(token, project_id):
    try:
        header, payload, signature = token.split('.')
        decode = lambda part: base64.urlsafe_b64decode(part + '=' * (-len(part) % 4))
        secret = os.environ['OMICSBASE_AUTH_SECRET']
        if not secret:
            return False
        expected = hmac.new(secret.encode(), f'{header}.{payload}'.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, decode(signature)):
            return False
        metadata, claims = json.loads(decode(header)), json.loads(decode(payload))
        return (metadata.get('alg') == 'HS256' and claims.get('aud') == 'omicsbase-openhands'
                and claims.get('iss') == 'librechat' and claims.get('purpose') == 'project'
                and claims.get('project_id') == project_id and claims.get('exp', 0) > time.time())
    except (ValueError, KeyError, TypeError, AttributeError):
        return False


class ProjectAccess(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        parts = request.url.path.strip('/').split('/')
        project_id = None
        if len(parts) >= 2 and parts[0] == 'projects':
            project_id = parts[1]
        elif len(parts) >= 3 and parts[:2] == ['api', 'projects']:
            project_id = parts[2]
        if project_id is not None:
            internal = request.headers.get('x-internal-secret', '')
            secret = os.environ.get('OMICSBASE_AUTH_SECRET', '')
            trusted = bool(secret and internal and hmac.compare_digest(internal, secret))
            token = request.cookies.get('omicsbase_project_' + project_id, '')
            if not trusted and not verify_project(token, project_id):
                return JSONResponse({'error': 'Project authorization required'}, status_code=401)
        return await call_next(request)
