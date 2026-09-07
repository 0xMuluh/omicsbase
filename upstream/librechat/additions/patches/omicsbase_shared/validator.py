import http.cookies
import json
import os
from openhands.storage.conversation.conversation_validator import ConversationValidator
from omicsbase_shared.identity import COOKIE, verify_ticket
from omicsbase_shared.registry import state_root


class OmicsBaseConversationValidator(ConversationValidator):
    async def validate(
        self,
        conversation_id: str,
        cookies_str: str,
        authorization_header: str | None = None,
    ) -> str | None:
        user_id = None
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
        if not user_id:
            path = state_root() / f'{conversation_id}.json'
            if path.exists():
                try:
                    user_id = json.loads(path.read_text()).get('user_id')
                except Exception:
                    pass
        metadata = await self._ensure_metadata_exists(conversation_id, user_id)
        return metadata.user_id or user_id
