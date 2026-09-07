import asyncio
import time
import docker
from fastapi.testclient import TestClient
from pydantic import SecretStr
from openhands.server.shared import config, conversation_manager
from openhands.storage.data_models.settings import Settings
from openhands.storage.settings.file_settings_store import FileSettingsStore
from omicsbase_shared.app import app
from omicsbase_shared.identity import runtime_name
from test_identity import ticket


async def seed():
    store = await FileSettingsStore.get_instance(config, None)
    await store.store(Settings(llm_model='openai/gpt-4o', llm_api_key=SecretStr('test-only-no-requests'), agent='CodeActAgent'))


asyncio.run(seed())
try:
    with TestClient(app) as client:
        client.cookies.set('omicsbase_openhands', ticket())
        ids = []
        for project in ['launch-study-a', 'launch-study-b']:
            response = client.post('/api/omicsbase/conversations', json={'ticket': ticket(purpose='launch', project_id=project)})
            assert response.status_code == 200, response.text
            ids.append(response.json()['conversation_id'])
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            runtimes = [conversation_manager._local_agent_loops_by_sid[sid].agent_session.runtime for sid in ids]
            if all(runtime and runtime.runtime_initialized for runtime in runtimes):
                break
            time.sleep(1)
        assert all(runtime and runtime.runtime_initialized for runtime in runtimes), 'Runtime initialization failed'
        assert runtimes[0].container.id == runtimes[1].container.id
        assert runtimes[0].project_dir == '/workspace/launch-study-a'
        assert runtimes[1].project_dir == '/workspace/launch-study-b'
        print('PASS: authenticated HTTP project launches create distinct conversations in one user container', flush=True)
finally:
    client = docker.from_env()
    try:
        client.containers.get(runtime_name('user-a')).remove(force=True)
    except docker.errors.NotFound:
        pass
    client.close()
