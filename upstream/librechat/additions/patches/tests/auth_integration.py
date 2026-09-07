import asyncio
import os
import time
from fastapi.testclient import TestClient
from openhands.server.shared import config
from openhands.storage.data_models.conversation_metadata import ConversationMetadata
from omicsbase_shared.app import app
from omicsbase_shared.stores import UserConversations, UserSettings, UserSecrets
from test_identity import ticket


async def seed():
    store = await UserConversations.get_instance(config, 'user-a')
    await store.save_metadata(ConversationMetadata('owned-chat', None, user_id='user-a'))
    other = await UserConversations.get_instance(config, 'user-b')
    assert not await other.exists('owned-chat')
    settings_a = await UserSettings.get_instance(config, 'user-a')
    settings_b = await UserSettings.get_instance(config, 'user-b')
    assert settings_a.path != settings_b.path
    secrets_a = await UserSecrets.get_instance(config, 'user-a')
    secrets_b = await UserSecrets.get_instance(config, 'user-b')
    assert secrets_a.path != secrets_b.path


asyncio.run(seed())
with TestClient(app) as client:
    assert client.get('/api/conversations').status_code == 401
    client.cookies.set('omicsbase_openhands', ticket())
    response = client.get('/api/conversations/owned-chat/events')
    assert response.status_code == 200, response.text
    client.cookies.set('omicsbase_openhands', ticket(sub='user-b'))
    assert client.get('/api/conversations/owned-chat/events').status_code == 404
    client.cookies.set('omicsbase_openhands', ticket(exp=0))
    assert client.get('/api/conversations').status_code == 401
print('PASS: real OpenHands HTTP authentication rejects anonymous/expired credentials and cross-user history access')
print('PASS: settings and secrets use independent per-user stores')
