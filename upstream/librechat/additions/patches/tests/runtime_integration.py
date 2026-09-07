"""Run inside an isolated OpenHands server container with Docker access and test-only mounts."""
import asyncio
import os
import time
import docker
from pathlib import Path
from openhands.core.config import OpenHandsConfig
from openhands.events.stream import EventStream
from openhands.events.action import CmdRunAction, FileReadAction
from openhands.llm.llm_registry import LLMRegistry
from openhands.storage.local import LocalFileStore
from omicsbase_shared.runtime import UserRuntime
from omicsbase_shared.registry import bind_project


async def main():
    config = OpenHandsConfig()
    config.sandbox.runtime_container_image = os.environ['TEST_RUNTIME_IMAGE']
    config.sandbox.local_runtime_url = 'http://host.docker.internal'
    config.enable_browser = False
    config.run_as_openhands = False
    config.security.security_analyzer = None
    store = LocalFileStore('/tmp/events')
    clients = []
    streams = []
    containers = set()
    def make(owner, project, conversation):
        bind_project(owner, project, conversation)
        stream = EventStream(conversation, store, owner)
        streams.append(stream)
        runtime = UserRuntime(config, stream, LLMRegistry(config), sid=conversation, headless_mode=True)
        clients.append(runtime)
        containers.add(runtime.container_name)
        return runtime
    try:
        a = make('test-user-a', 'study-a', 'chat-a')
        b = make('test-user-a', 'study-b', 'chat-b')
        await asyncio.gather(a.connect(), b.connect())
        assert a.container.id == b.container.id
        assert a.sid != b.sid
        print('PASS: concurrent conversations share exactly one user container', flush=True)
        def run(runtime, command):
            action = CmdRunAction(command=command)
            action.set_hard_timeout(30, blocking=True)
            result = runtime.run_action(action)
            assert getattr(result, 'exit_code', None) == 0, result
            return result.content
        await asyncio.to_thread(run, a, 'export STUDY_MARKER=alpha; printf "alpha-data" > result.txt')
        await asyncio.to_thread(run, b, 'export STUDY_MARKER=beta; printf "beta-data" > result.txt')
        result_a, result_b = await asyncio.gather(
            asyncio.to_thread(run, a, 'pwd; echo "$STUDY_MARKER"; cat result.txt'),
            asyncio.to_thread(run, b, 'pwd; echo "$STUDY_MARKER"; cat result.txt'),
        )
        assert '/workspace/study-a' in result_a and 'alpha-data' in result_a and 'beta-data' not in result_a, result_a
        assert '/workspace/study-b' in result_b and 'beta-data' in result_b and 'alpha-data' not in result_b, result_b
        print('PASS: concurrent commands retain separate cwd, environment, files and results', flush=True)
        await asyncio.to_thread(run, a, "Rscript -e 'stopifnot(1 + 1 == 2)'")
        print('PASS: R executes in the shared runtime', flush=True)
        a.close()
        await a.delete('chat-a')
        await asyncio.to_thread(run, b, 'test "$STUDY_MARKER" = beta')
        print('PASS: close/delete conversation leaves the shared runtime usable', flush=True)
        c = make('test-user-b', 'study-c', 'chat-c')
        await c.connect()
        assert c.container.id != b.container.id
        await asyncio.to_thread(run, c, 'test ! -e /workspace/study-a/result.txt')
        print('PASS: a different user gets a different container and filesystem mount', flush=True)
        # Reattach with the same conversation ID: history remains outside the container.
        restored = UserRuntime(config, streams[0], LLMRegistry(config), sid='chat-a', headless_mode=True, attach_to_existing=True)
        clients.append(restored)
        await restored.connect()
        assert restored.container.id == b.container.id
        await asyncio.to_thread(run, restored, 'test "$STUDY_MARKER" = alpha; test -f result.txt')
        print('PASS: reconnect restores the conversation shell in the existing user runtime', flush=True)
    finally:
        for client in clients:
            client.close()
        for stream in streams:
            stream.close()
        docker_client = docker.from_env()
        for name in containers:
            try:
                docker_client.containers.get(name).remove(force=True)
            except docker.errors.NotFound:
                pass
        docker_client.close()


asyncio.run(main())
