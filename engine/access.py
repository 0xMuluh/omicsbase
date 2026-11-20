"""Who may use the engine: LibreChat with the shared secret, browsers with a project token."""
import base64
import hashlib
import hmac
import json
import os
import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


def engine_secret():
    """The secret shared with LibreChat; the Reports secret when not set separately."""
    return os.environ.get('OMICSBASE_ENGINE_SECRET') or os.environ.get('OMICSBASE_AUTH_SECRET', '')


def verify_project(token, project_id):
    if not token:
        return False
    try:
        header, payload, signature = token.split('.')
        decode = lambda part: base64.urlsafe_b64decode(part + '=' * (-len(part) % 4))
        secret = engine_secret()
        if not secret:
            return False
        expected = hmac.new(secret.encode(), f'{header}.{payload}'.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, decode(signature)):
            return False
        metadata, claims = json.loads(decode(header)), json.loads(decode(payload))
        return (metadata.get('alg') == 'HS256' and claims.get('aud') == 'omicsbase-openhands'
                and claims.get('iss') == 'librechat' and claims.get('purpose') in ('project', 'launch')
                and claims.get('project_id') == project_id and claims.get('exp', 0) > time.time())
    except (ValueError, KeyError, TypeError, AttributeError):
        return False


def internal_caller(request):
    """LibreChat, holding the secret the engine shares with it."""
    provided = request.headers.get('x-internal-secret', '')
    secret = engine_secret()
    return bool(secret and provided and hmac.compare_digest(provided, secret))


class ProjectAccess(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        parts = request.url.path.strip('/').split('/')
        # The notes tool connection (MCP) runs R, so it is LibreChat's alone.
        if parts[0] in ('sse', 'messages') and not internal_caller(request):
            return JSONResponse({'error': 'Unauthorized'}, status_code=401)
        project_id = None
        if len(parts) >= 2 and parts[0] == 'projects':
            project_id = parts[1]
        elif len(parts) >= 3 and parts[:2] == ['api', 'projects']:
            project_id = parts[2]
        token = None
        if project_id is not None:
            trusted = internal_caller(request)
            token = (
                request.query_params.get('token')
                or request.query_params.get('ticket')
                or request.headers.get('authorization', '').replace('Bearer ', '').strip()
                or request.cookies.get('omicsbase_project_' + project_id, '')
            )
            if not trusted and not verify_project(token, project_id):
                return JSONResponse({'error': 'Project authorization required'}, status_code=401)
        response = await call_next(request)
        if project_id is not None and token and verify_project(token, project_id):
            response.set_cookie(
                'omicsbase_project_' + project_id,
                token,
                path='/',
                samesite='lax',
                httponly=True,
            )
        return response
