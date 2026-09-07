"""Push bus for workspace job and project state changes."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections import defaultdict
from typing import Any, AsyncIterator

from app.config import settings

logger = logging.getLogger(__name__)

CHANNEL_PREFIX = "omicsbase:project:"
_local_subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)
_local_lock = threading.Lock()
_force_local = False
_redis_client = None
_redis_lock = threading.Lock()


def project_channel(project_id: str) -> str:
    return f"{CHANNEL_PREFIX}{project_id}"


def publish_project_event(project_id: str, event: dict[str, Any] | None = None) -> None:
    """Notify live workspace listeners that a project changed."""
    payload = {
        "project_id": str(project_id),
        "type": "workspace_changed",
        **(event or {}),
    }
    encoded = json.dumps(payload, default=str)
    if not _force_local:
        _publish_redis(project_id, encoded)
    _publish_local(project_id, encoded)


def _get_redis_client():
    global _redis_client
    with _redis_lock:
        if _redis_client is not None:
            try:
                _redis_client.ping()
                return _redis_client
            except Exception:
                try:
                    _redis_client.close()
                except Exception:
                    pass
                _redis_client = None
        try:
            import redis

            client = redis.Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=0.5,
                socket_timeout=1.0,
                health_check_interval=30,
            )
            client.ping()
            _redis_client = client
            return _redis_client
        except Exception as exc:
            logger.debug("Redis client unavailable: %s", exc)
            return None


def _publish_redis(project_id: str, encoded: str) -> None:
    client = _get_redis_client()
    if client is None:
        return
    try:
        client.publish(project_channel(project_id), encoded)
    except Exception as exc:
        logger.debug("Redis project-event publish failed: %s", exc)


def _publish_local(project_id: str, encoded: str) -> None:
    with _local_lock:
        subscribers = list(_local_subscribers.get(str(project_id), []))
    for queue in subscribers:
        try:
            queue.put_nowait(encoded)
        except asyncio.QueueFull:
            continue


async def subscribe_project_events(project_id: str) -> AsyncIterator[dict[str, Any]]:
    """Yield push notifications for one project, with Redis or in-process fallback."""
    if not _force_local:
        redis_stream = await _try_redis_subscriber(project_id)
        if redis_stream is not None:
            async for event in redis_stream:
                yield event
            return

    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
    key = str(project_id)
    with _local_lock:
        _local_subscribers[key].append(queue)
    try:
        while True:
            encoded = await queue.get()
            try:
                yield json.loads(encoded)
            except json.JSONDecodeError:
                continue
    finally:
        with _local_lock:
            subscribers = _local_subscribers.get(key, [])
            if queue in subscribers:
                subscribers.remove(queue)
            if not subscribers:
                _local_subscribers.pop(key, None)


async def _try_redis_subscriber(project_id: str):
    try:
        import redis.asyncio as redis_async

        client = redis_async.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=1.0,
        )
        await asyncio.wait_for(client.ping(), timeout=0.75)
        pubsub = client.pubsub()
        await pubsub.subscribe(project_channel(project_id))
    except Exception as exc:
        logger.debug("Redis project-event subscribe unavailable: %s", exc)
        return None

    async def _stream() -> AsyncIterator[dict[str, Any]]:
        try:
            while True:
                try:
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True, timeout=None),
                        timeout=1.0,
                    )
                except asyncio.TimeoutError:
                    continue
                if not message:
                    continue
                data = message.get("data")
                if not isinstance(data, str):
                    continue
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    continue
        finally:
            try:
                await pubsub.unsubscribe(project_channel(project_id))
                await pubsub.aclose()
                await client.aclose()
            except Exception:
                pass

    return _stream()


def extract_execution_error_diagnostics(logs: list[str]) -> str:
    """Extract structured R and Quarto compilation/execution error messages from raw run logs.

    Filters log lines for 'Error:', 'Execution halted', 'Quarto error', or exception traces
    to feed the automated AI repair loop.
    """
    if not logs:
        return "No execution log output recorded."

    error_lines: list[str] = []
    capture = False
    for line in logs:
        text = str(line).strip()
        if any(keyword in text for keyword in ("Error:", "Error in ", "Execution halted", "Quarto error", "QuartoError", "Quitting from lines")):
            capture = True
            error_lines.append(text)
        elif capture and (text.startswith("Line ") or text.startswith("  ") or "Calls:" in text or "In addition:" in text):
            error_lines.append(text)
        elif capture and not text:
            capture = False

    if error_lines:
        return "\n".join(error_lines[:30])
    return "\n".join(logs[-15:])

