from fastapi import HTTPException
from openhands.server.user_auth.default_user_auth import DefaultUserAuth
from openhands.server.user_auth.user_auth import AuthType
from omicsbase_shared.identity import COOKIE, verify_ticket


class OmicsBaseAuth(DefaultUserAuth):
    async def get_user_id(self):
        return self.identity

    def get_auth_type(self):
        return AuthType.COOKIE

    @classmethod
    async def get_instance(cls, request):
        token = request.cookies.get(COOKIE, '')
        authorization = request.headers.get('authorization', '')
        if authorization.startswith('Bearer '):
            token = authorization[7:]
        try:
            claims = verify_ticket(token)
            bootstrap = request.url.path in {'/api/omicsbase/conversations', '/api/omicsbase/session'}
            if claims.get('purpose') != 'session' and not (bootstrap and claims.get('purpose') == 'launch' and authorization.startswith('Bearer ')):
                raise ValueError('Invalid credential purpose')
        except (ValueError, KeyError, TypeError):
            raise HTTPException(401, 'Open the workspace from LibreChat to authenticate')
        auth = cls()
        auth.identity = claims['sub']
        return auth
