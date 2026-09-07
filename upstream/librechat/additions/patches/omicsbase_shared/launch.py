import uuid
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from openhands.server.user_auth.user_auth import get_user_auth
from openhands.server.services.conversation_service import create_new_conversation
from omicsbase_shared.identity import verify_ticket
from omicsbase_shared.registry import bind_project

router = APIRouter()

class LaunchRequest(BaseModel):
    ticket: str


@router.post('/api/omicsbase/conversations')
async def launch(data: LaunchRequest, request: Request):
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
    return {'status': 'ok', 'conversation_id': conversation_id, 'conversation_status': info.status}

