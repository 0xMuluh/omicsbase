"""Keep one headless OpenCode server alive for OmicsBase.

OmicsBase is the product shell; OpenCode is the coding runtime. The frontend
and Celery jobs talk to OmicsBase, which relays to ``opencode serve`` over
HTTP instead of spawning disposable ``opencode run`` processes.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.opencode_client import build_opencode_env, resolve_opencode_bin

logger = logging.getLogger(__name__)

_server_lock = asyncio.Lock()
_server_proc: asyncio.subprocess.Process | None = None
_server_base_url: str | None = None


def configured_server_url() -> str | None:
    value = (settings.opencode_server_url or os.environ.get("OPENCODE_SERVER_URL") or "").strip()
    return value.rstrip("/") if value else None


def server_base_url() -> str:
    configured = configured_server_url()
    if configured:
        return configured
    if _server_base_url:
        return _server_base_url
    port = int(settings.opencode_server_port or 4096)
    host = (settings.opencode_server_hostname or "127.0.0.1").strip() or "127.0.0.1"
    return f"http://{host}:{port}"


async def _health_ok(base_url: str) -> bool:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{base_url}/global/health")
            if response.status_code != 200:
                return False
            payload = response.json()
            return bool(payload.get("healthy"))
    except Exception:
        return False


async def ensure_server() -> str:
    """Return the base URL for a healthy OpenCode server, starting one if needed."""
    configured = configured_server_url()
    if configured:
        if not await _health_ok(configured):
            raise RuntimeError(f"OpenCode server unhealthy at {configured}")
        return configured

    global _server_proc, _server_base_url
    async with _server_lock:
        base = server_base_url()
        if await _health_ok(base):
            _server_base_url = base
            return base

        if not settings.opencode_server_autostart:
            raise RuntimeError(
                "OpenCode server is not running and OPENCODE_SERVER_AUTOSTART is disabled. "
                "Start `opencode serve` or set OPENCODE_SERVER_URL."
            )

        if _server_proc and _server_proc.returncode is None:
            for _ in range(20):
                await asyncio.sleep(0.25)
                if await _health_ok(base):
                    _server_base_url = base
                    return base
            _server_proc.kill()
            await _server_proc.wait()
            _server_proc = None

        opencode_bin = resolve_opencode_bin()
        host = (settings.opencode_server_hostname or "127.0.0.1").strip() or "127.0.0.1"
        port = int(settings.opencode_server_port or 4096)
        env = build_opencode_env()
        password = (settings.opencode_server_password or os.environ.get("OPENCODE_SERVER_PASSWORD") or "").strip()
        if password:
            env.setdefault("OPENCODE_SERVER_PASSWORD", password)
        username = (settings.opencode_server_username or os.environ.get("OPENCODE_SERVER_USERNAME") or "").strip()
        if username:
            env.setdefault("OPENCODE_SERVER_USERNAME", username)

        cmd = [
            opencode_bin,
            "serve",
            "--hostname",
            host,
            "--port",
            str(port),
        ]
        logger.info("Starting OpenCode server at %s:%s", host, port)
        _server_proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        _server_base_url = base

        for _ in range(60):
            if _server_proc.returncode is not None:
                stderr = b""
                if _server_proc.stderr is not None:
                    stderr = await _server_proc.stderr.read()
                raise RuntimeError(
                    f"OpenCode server exited early ({_server_proc.returncode}): "
                    f"{stderr.decode('utf-8', errors='replace')[:500]}"
                )
            if await _health_ok(base):
                return base
            await asyncio.sleep(0.25)

        raise RuntimeError(f"OpenCode server failed to become healthy at {base}")


async def shutdown_server() -> None:
    global _server_proc, _server_base_url
    async with _server_lock:
        if _server_proc and _server_proc.returncode is None:
            _server_proc.terminate()
            try:
                await asyncio.wait_for(_server_proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                _server_proc.kill()
                await _server_proc.wait()
        _server_proc = None
        if not configured_server_url():
            _server_base_url = None


def reset_for_tests() -> None:
    global _server_proc, _server_base_url
    _server_proc = None
    _server_base_url = None
