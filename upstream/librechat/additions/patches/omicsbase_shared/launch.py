import uuid
import os
import json
from urllib.parse import urlsplit
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from openhands.server.user_auth.user_auth import get_user_auth
from openhands.server.services.conversation_service import create_new_conversation
from omicsbase_shared.identity import COOKIE, issue_session_ticket, verify_ticket, workspace_session_ttl
from omicsbase_shared.registry import bind_project, state_root
from omicsbase_shared.identity import identifier
from omicsbase_shared.sessions import revoke

router = APIRouter()

class LaunchRequest(BaseModel):
    ticket: str


@router.post('/api/omicsbase/conversations')
async def launch(data: LaunchRequest, request: Request, response: Response):
    auth = await get_user_auth(request)
    user_id = await auth.get_user_id()
    try:
        claims = verify_ticket(data.ticket)
        if claims['sub'] != user_id or claims.get('purpose') != 'launch':
            raise ValueError('Invalid launch ticket')
        conversation_id = uuid.uuid4().hex
        project_dir = bind_project(user_id, claims['project_id'], conversation_id)
    except (ValueError, KeyError, TypeError):
        raise HTTPException(403, 'Invalid project authorization')
    secrets = await auth.get_user_secrets()
    info = await create_new_conversation(
        user_id=user_id,
        git_provider_tokens=await auth.get_provider_tokens(),
        custom_secrets=secrets.custom_secrets if secrets else None,
        selected_repository=None,
        selected_branch=None,
        initial_user_msg=None,
        image_urls=None,
        replay_json=None,
        conversation_id=conversation_id,
        conversation_instructions=(
            f'This conversation works in {project_dir}. Use that directory for all project work. '
            'The runtime and installed packages are shared with your other conversations; '
            'other project directories must only be changed when explicitly requested. '
            f'The OmicsBase project ID is {claims["project_id"]}. '
            'Run R analysis through Rscript in the terminal. Browser state is shared; open '
            'the intended URL before inspecting a page.'
        ),
    )
    set_session(response, request, user_id)
    return {'status': 'ok', 'conversation_id': conversation_id, 'conversation_status': info.status}



def set_session(response, request, user_id):
    session_ttl = workspace_session_ttl()
    session_token = issue_session_ticket(user_id, expires_in=session_ttl)
    response.set_cookie(
        key=COOKIE,
        value=session_token,
        httponly=True,
        secure=urlsplit(os.environ.get('OMICSBASE_OPENHANDS_PUBLIC_URL', str(request.base_url))).scheme == 'https',
        samesite='lax',
        path='/',
        max_age=session_ttl,
    )
    response.headers['Cache-Control'] = 'no-store'


class SessionRequest(LaunchRequest):
    conversation_id: str | None = None


@router.post('/api/omicsbase/session')
async def renew_session(data: SessionRequest, request: Request, response: Response):
    auth = await get_user_auth(request)
    user_id = await auth.get_user_id()
    try:
        claims = verify_ticket(data.ticket)
        if claims['sub'] != user_id or claims.get('purpose') != 'launch':
            raise ValueError('Invalid launch credential')
        if data.conversation_id:
            record = state_root() / f'{identifier(data.conversation_id)}.json'
            binding = json.loads(record.read_text())
            if binding['user_id'] != user_id or binding['project_id'] != claims['project_id']:
                raise ValueError('Conversation/project mismatch')
    except (ValueError, KeyError, TypeError, FileNotFoundError):
        raise HTTPException(403, 'Invalid workspace authorization')
    set_session(response, request, user_id)
    return {'status': 'ok', 'expires_in': workspace_session_ttl()}


@router.post('/api/omicsbase/revoke')
async def revoke_sessions(request: Request):
    try:
        header = request.headers.get('authorization', '')
        if not header.startswith('Bearer '):
            raise ValueError('Missing authorization')
        claims = verify_ticket(header[7:])
        if claims.get('purpose') != 'logout':
            raise ValueError('Invalid purpose')
        revoke(claims['sub'])
    except (ValueError, KeyError, TypeError):
        raise HTTPException(401, 'Invalid revocation authorization')
    return {'status': 'ok'}
