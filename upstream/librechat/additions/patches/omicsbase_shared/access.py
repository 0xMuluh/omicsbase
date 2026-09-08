"""Authorize custom editor/report routes with the same owner as the workspace."""
import json
from fastapi import Request, HTTPException
from openhands.server.user_auth.user_auth import get_user_auth
from omicsbase_shared.identity import identifier
from omicsbase_shared.registry import state_root


async def authorize_project(request: Request):
    auth = await get_user_auth(request)
    user_id = await auth.get_user_id()
    try:
        conversation_id = identifier(request.path_params['conversation_id'])
        record = json.loads((state_root() / f'{conversation_id}.json').read_text())
        if record['user_id'] != user_id:
            raise ValueError('Owner mismatch')
    except (ValueError, KeyError, FileNotFoundError):
        raise HTTPException(404, 'Workspace not found')
