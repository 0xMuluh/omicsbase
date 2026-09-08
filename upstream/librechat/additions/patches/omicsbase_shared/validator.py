import http.cookies
from openhands.storage.conversation.conversation_validator import ConversationValidator
from socketio.exceptions import ConnectionRefusedError
from omicsbase_shared.identity import COOKIE, verify_ticket


class OmicsBaseConversationValidator(ConversationValidator):
    async def validate(
        self,
        conversation_id: str,
        cookies_str: str,
        authorization_header: str | None = None,
    ) -> str | None:
        user_id = None
        claims = {}
        token = ''
        if cookies_str:
            try:
                c = http.cookies.SimpleCookie()
                c.load(cookies_str)
                if COOKIE in c:
                    token = c[COOKIE].value
            except Exception:
                pass
        if not token and authorization_header and authorization_header.startswith('Bearer '):
            token = authorization_header[7:]
        if token:
            try:
                claims = verify_ticket(token)
                user_id = claims.get('sub')
            except Exception:
                pass
        if not user_id or claims.get('purpose') != 'session':
            raise ConnectionRefusedError('Valid workspace session required')
        metadata = await self._ensure_metadata_exists(conversation_id, user_id)
        if metadata.user_id != user_id:
            raise ConnectionRefusedError('Conversation owner mismatch')
        return user_id
