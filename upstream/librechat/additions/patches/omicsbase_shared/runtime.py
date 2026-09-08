import copy
import hashlib
import hmac
import os
from functools import lru_cache
import docker
from pathlib import Path

from openhands.runtime.impl.docker.docker_runtime import DockerRuntime
from openhands.runtime.impl.action_execution.action_execution_client import ActionExecutionClient
from openhands.runtime.runtime_status import RuntimeStatus
from openhands.events import EventStreamSubscriber
from openhands.utils.async_utils import call_sync_from_async
from openhands.utils.shutdown_listener import remove_shutdown_listener
from omicsbase_shared.identity import identifier, runtime_name, user_root
from omicsbase_shared.registry import lock, conversation_project


@lru_cache(maxsize=1)
def host_mounts():
    client = docker.from_env()
    try:
        mounts = client.containers.get(os.environ['HOSTNAME']).attrs['Mounts']
        sources = {mount['Destination']: mount['Source'] for mount in mounts}
        return sources['/opt/workspace_base'], str(Path(sources['/app/omicsbase_shared']).parent)
    finally:
        client.close()


class UserRuntime(DockerRuntime):
    def __init__(self, config, event_stream, llm_registry, sid='default', user_id=None, **kwargs):
        owner = identifier(user_id or getattr(event_stream, 'user_id', None))
        self.owner = owner
        self.project_dir = conversation_project(owner, sid)
        config = copy.deepcopy(config)
        root = user_root(owner)
        root.mkdir(parents=True, exist_ok=True)
        host_root = Path(host_mounts()[0]) / 'users' / owner
        config.workspace_base = str(root)
        config.workspace_mount_path = str(host_root)
        config.workspace_mount_path_in_sandbox = '/workspace'
        config.sandbox.volumes = None
        config.sandbox.keep_runtime_alive = True
        config.sandbox.runtime_startup_env_vars['OMICSBASE_SHARED_RUNTIME'] = '1'
        key = hmac.new(os.environ['OMICSBASE_AUTH_SECRET'].encode(), owner.encode(), hashlib.sha256).hexdigest()
        config.sandbox.runtime_startup_env_vars['SESSION_API_KEY'] = key
        config.enable_browser = False
        # The analysis workspace uses R; no unused Python kernel, VS Code server, or headless browser.
        if kwargs.get('plugins'):
            kwargs['plugins'] = [p for p in kwargs['plugins'] if p.name not in ('jupyter', 'vscode')]
        kwargs['headless_mode'] = True
        super().__init__(config, event_stream, llm_registry, sid=sid, user_id=owner, **kwargs)
        # Ensure VS Code plugin and port allocation are completely disabled
        self.plugins = [p for p in self.plugins if p.name not in ('jupyter', 'vscode')]
        self._vscode_enabled = False
        if getattr(self, '_vscode_port_lock', None):
            try:
                self._vscode_port_lock.release()
            except Exception:
                pass
            self._vscode_port_lock = None
        self._vscode_port = -1

        # User runtimes outlive conversation and server clients.
        if DockerRuntime._shutdown_listener_id:
            remove_shutdown_listener(DockerRuntime._shutdown_listener_id)
        self.container_name = runtime_name(owner)
        self._session_api_key = key
        self.session.headers['X-Session-API-Key'] = key
        self.session.headers['X-OmicsBase-Conversation'] = identifier(sid)
        self.session.headers['X-OmicsBase-Project'] = self.project_dir

    @property
    def public_base_url(self) -> str:
        return os.environ.get(
            'OMICSBASE_OPENHANDS_PUBLIC_URL',
            'http://localhost:3001',
        ).rstrip('/')

    @property
    def vscode_url(self) -> str | None:
        sid = getattr(self, 'sid', None)
        if not sid:
            return None
        return f"{self.public_base_url}/api/omicsbase/editor/{sid}"

    @property
    def web_hosts(self) -> dict[str, int]:
        sid = getattr(self, 'sid', None)
        if not sid:
            return {}
        return {
            f"{self.public_base_url}/api/omicsbase/report/{sid}": 3001
        }

    @property
    def session_api_key(self):
        return getattr(self, '_session_api_key', None)

    def _process_volumes(self):
        volumes = super()._process_volumes()
        source = host_mounts()[1]
        volumes[str(Path(source) / 'action_execution_server.py')] = {
            'bind': '/openhands/code/openhands/runtime/action_execution_server.py', 'mode': 'ro'
        }
        return volumes

    async def connect(self):
        # File locks cover concurrent sessions and multiple OpenHands worker processes.
        await call_sync_from_async(self._connect_locked)
        self.set_runtime_status(RuntimeStatus.READY)

    def _connect_locked(self):
        import asyncio
        with lock(self.container_name):
            asyncio.run(super().connect())

    def close(self, rm_all_containers=None):
        # Conversation clients own their HTTP connection, never the user's container.
        if getattr(self, '_runtime_closed', False):
            return
        if getattr(self, 'event_stream', None):
            self.event_stream.unsubscribe(EventStreamSubscriber.RUNTIME, self.sid)
        ActionExecutionClient.close(self)
        if getattr(self, 'log_streamer', None):
            self.log_streamer.close()
        self._release_port_locks()

    @classmethod
    async def delete(cls, conversation_id):
        # Deleting one conversation must not delete the other conversations' runtime.
        return None

    def pause(self):
        return None

    def resume(self):
        return None
