"""Persistent stdio client for the Codex app-server JSON-RPC protocol."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import threading
from collections.abc import Mapping
from concurrent.futures import Future as ConcurrentFuture
from dataclasses import dataclass
from typing import Any


class CodexAppServerError(RuntimeError):
    """The app-server process or JSON-RPC connection failed."""


@dataclass(frozen=True)
class ServerMessage:
    """One app-server notification or server-initiated request."""

    method: str
    params: dict[str, Any]
    request_id: str | int | None = None


class _AppServerConnection:
    def __init__(self, executable: str, env: Mapping[str, str]) -> None:
        self.executable = executable
        self.env = dict(env)
        self.process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_tail = bytearray()
        self._start_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._request_id = 0
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._subscribers: dict[str, set[asyncio.Queue[ServerMessage]]] = {}
        self._all_subscribers: set[asyncio.Queue[ServerMessage]] = set()
        self._closed = False

    async def start(self) -> None:
        async with self._start_lock:
            if self.process is not None and self.process.returncode is None:
                return
            if self._closed:
                raise CodexAppServerError("Codex app-server connection is closed")
            self.process = await asyncio.create_subprocess_exec(
                self.executable,
                "app-server",
                "--listen",
                "stdio://",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self.env,
                start_new_session=True,
                limit=8 * 1024 * 1024,
            )
            self._reader_task = asyncio.create_task(
                self._read_stdout(), name="codex-app-server-stdout"
            )
            assert self.process.stderr is not None
            self._stderr_task = asyncio.create_task(
                self._drain_stderr(), name="codex-app-server-stderr"
            )
            await self._request_without_start(
                "initialize",
                {
                    "clientInfo": {
                        "name": "omicsbase",
                        "title": "OmicsBase",
                        "version": "0.1.0",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            )
            await self._notify_without_start("initialized", {})

    async def request(self, method: str, params: Mapping[str, Any]) -> Any:
        await self.start()
        return await self._request_without_start(method, params)

    async def notify(self, method: str, params: Mapping[str, Any]) -> None:
        await self.start()
        await self._notify_without_start(method, params)

    async def respond(self, request_id: str | int, result: Mapping[str, Any]) -> None:
        await self.start()
        await self._write({"id": request_id, "result": dict(result)})

    async def respond_error(
        self,
        request_id: str | int,
        *,
        code: int,
        message: str,
    ) -> None:
        await self.start()
        await self._write(
            {"id": request_id, "error": {"code": int(code), "message": str(message)}}
        )

    async def subscribe(self, thread_id: str | None = None) -> asyncio.Queue[ServerMessage]:
        await self.start()
        queue: asyncio.Queue[ServerMessage] = asyncio.Queue()
        if thread_id:
            self._subscribers.setdefault(thread_id, set()).add(queue)
        else:
            self._all_subscribers.add(queue)
        return queue

    async def move_subscription(
        self,
        queue: asyncio.Queue[ServerMessage],
        *,
        old_thread_id: str | None,
        new_thread_id: str,
    ) -> None:
        if old_thread_id:
            subscribers = self._subscribers.get(old_thread_id)
            if subscribers:
                subscribers.discard(queue)
                if not subscribers:
                    self._subscribers.pop(old_thread_id, None)
        else:
            self._all_subscribers.discard(queue)
        self._subscribers.setdefault(new_thread_id, set()).add(queue)

    async def unsubscribe(
        self,
        queue: asyncio.Queue[ServerMessage],
        thread_id: str | None,
    ) -> None:
        if thread_id:
            subscribers = self._subscribers.get(thread_id)
            if subscribers:
                subscribers.discard(queue)
                if not subscribers:
                    self._subscribers.pop(thread_id, None)
        else:
            self._all_subscribers.discard(queue)

    async def close(self) -> None:
        self._closed = True
        process = self.process
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
        if self._stderr_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._stderr_task
        self.process = None

    async def _request_without_start(
        self, method: str, params: Mapping[str, Any]
    ) -> Any:
        self._request_id += 1
        request_id = self._request_id
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._write(
                {"method": method, "id": request_id, "params": dict(params)}
            )
            return await future
        finally:
            self._pending.pop(request_id, None)

    async def _notify_without_start(
        self, method: str, params: Mapping[str, Any]
    ) -> None:
        await self._write({"method": method, "params": dict(params)})

    async def _write(self, payload: Mapping[str, Any]) -> None:
        process = self.process
        if process is None or process.returncode is not None or process.stdin is None:
            raise CodexAppServerError("Codex app-server is not running")
        encoded = json.dumps(dict(payload), separators=(",", ":"), default=str).encode()
        async with self._write_lock:
            process.stdin.write(encoded + b"\n")
            try:
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise CodexAppServerError("Codex app-server connection closed") from exc

    async def _read_stdout(self) -> None:
        process = self.process
        assert process is not None and process.stdout is not None
        failure: Exception | None = None
        try:
            while True:
                raw_line = await process.stdout.readline()
                if not raw_line:
                    break
                try:
                    payload = json.loads(raw_line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                if "id" in payload and "method" not in payload:
                    self._resolve_response(payload)
                    continue
                method = str(payload.get("method") or "").strip()
                if not method:
                    continue
                params = payload.get("params")
                if not isinstance(params, dict):
                    params = {}
                request_id = payload.get("id") if "id" in payload else None
                self._publish(ServerMessage(method, params, request_id))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive process boundary
            failure = exc
        finally:
            if not self._closed:
                stderr = bytes(self._stderr_tail).decode(
                    "utf-8", errors="replace"
                ).strip()
                detail = stderr or str(failure or "Codex app-server exited unexpectedly")
                error = CodexAppServerError(detail[:2_000])
                for future in list(self._pending.values()):
                    if not future.done():
                        future.set_exception(error)
                self._publish(
                    ServerMessage("connection/error", {"message": str(error)})
                )

    async def _drain_stderr(self) -> None:
        process = self.process
        assert process is not None and process.stderr is not None
        while True:
            chunk = await process.stderr.read(8_192)
            if not chunk:
                return
            self._stderr_tail.extend(chunk)
            if len(self._stderr_tail) > 65_536:
                del self._stderr_tail[:-65_536]

    def _resolve_response(self, payload: Mapping[str, Any]) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, int):
            return
        future = self._pending.get(request_id)
        if future is None or future.done():
            return
        error = payload.get("error")
        if isinstance(error, Mapping):
            message = str(error.get("message") or "Codex app-server request failed")
            code = error.get("code")
            future.set_exception(CodexAppServerError(f"{message} (code {code})"))
        else:
            future.set_result(payload.get("result"))

    def _publish(self, message: ServerMessage) -> None:
        thread_id = str(message.params.get("threadId") or "").strip()
        queues = set(self._all_subscribers)
        if thread_id:
            queues.update(self._subscribers.get(thread_id, set()))
        for queue in queues:
            queue.put_nowait(message)


class CodexAppServer:
    """Own one app-server process on a dedicated event loop.

    OmicsBase invokes workspace turns from both long-lived ASGI loops and
    short-lived Celery loops. Keeping the transport on its own loop makes one
    app-server process reusable from either execution path.
    """

    def __init__(self, executable: str, env: Mapping[str, str]) -> None:
        self._executable = executable
        self._env = dict(env)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connection: _AppServerConnection | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="omicsbase-codex-app-server",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=10.0):
            raise CodexAppServerError("Timed out starting the Codex app-server loop")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._connection = _AppServerConnection(self._executable, self._env)
        self._ready.set()
        loop.run_forever()
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()

    def submit(self, coroutine: Any) -> ConcurrentFuture[Any]:
        loop = self._loop
        if loop is None or not loop.is_running():
            raise CodexAppServerError("Codex app-server loop is not running")
        return asyncio.run_coroutine_threadsafe(coroutine, loop)

    async def call(self, coroutine: Any) -> Any:
        return await asyncio.wrap_future(self.submit(coroutine))

    @property
    def connection(self) -> _AppServerConnection:
        if self._connection is None:
            raise CodexAppServerError("Codex app-server loop has not initialized")
        return self._connection

    async def close(self) -> None:
        loop = self._loop
        if loop is None:
            return
        if loop.is_running() and self._connection is not None:
            await self.call(self._connection.close())
            loop.call_soon_threadsafe(loop.stop)
        if self._thread.is_alive():
            await asyncio.to_thread(self._thread.join, 10.0)


__all__ = ["CodexAppServer", "CodexAppServerError", "ServerMessage"]
