"""Live OpenCode HTTP/SSE relay."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import mimetypes
from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path
from typing import Any

from app.config import settings

from app.services.opencode_config import (
    _active_provider_id,
    resolve_model_spec,
)
from app.services.opencode_protocol import (
    TurnOutcome,
    build_final_event,
    clarification_request_from_question_event,
    format_opencode_error,
    summarize_turn,
)
from app.services.opencode_requests import (
    basic_auth as _basic_auth,
    native_file_part as _native_file_part,
    prompt_payload as _prompt_payload,
)
from app.services.opencode.prompts import compose_user_prompt, workspace_system_prompt
from app.services.opencode.sessions import (
    ensure_project_runtime,
    ensure_session,
    load_message_roles,
    load_messages,
    map_part_event,
    message_info,
    opencode_status_is_active,
    part_text_delta,
    permission_id_from_event,
)

logger = logging.getLogger(__name__)
_POLL_INTERVAL_SECONDS = 1.0
_IDLE_DRAIN_SECONDS = 2.0
_IDLE_CONFIRM_POLLS = 3
_STARTUP_IDLE_POLLS = 60

_SPREADSHEET_SUFFIXES = {".xlsx", ".xls"}


def _spreadsheet_text_content(source: Path, max_bytes: int) -> bytes:
    """Encode only the spreadsheet prefix that fits the native context budget."""
    if source.suffix.lower() == ".xls":
        raise RuntimeError("Legacy .xls files cannot be converted by the installed spreadsheet reader")
    try:
        import openpyxl
    except ImportError as exc:
        raise RuntimeError("openpyxl is required to attach spreadsheet contents") from exc

    workbook = None
    payload = bytearray()

    def append_line(line: str) -> bool:
        encoded = (line + "\n").encode("utf-8")
        if max_bytes and len(payload) + len(encoded) > max_bytes:
            return False
        payload.extend(encoded)
        return True

    try:
        workbook = openpyxl.load_workbook(
            str(source),
            read_only=True,
            data_only=True,
        )
        append_line(f"# Tabular representation of {source.name}")
        truncated = False
        for worksheet in workbook.worksheets:
            if not append_line(f"\n## Sheet: {worksheet.title}"):
                truncated = True
                break
            for row in worksheet.iter_rows(values_only=True):
                cells = list(row)
                while cells and cells[-1] is None:
                    cells.pop()
                if not cells:
                    continue
                rendered: list[str] = []
                for value in cells:
                    cell_text = "" if value is None else str(value)
                    rendered.append(
                        cell_text
                        .replace("\\", "\\\\")
                        .replace("\t", "\\t")
                        .replace("\r", "\\r")
                        .replace("\n", "\\n")
                    )
                if not append_line("\t".join(rendered)):
                    truncated = True
                    break
            if truncated:
                break
        if truncated:
            marker = b"# Truncated: inspect the staged workbook with workspace tools.\n"
            if not max_bytes or len(payload) + len(marker) <= max_bytes:
                payload.extend(marker)
        return bytes(payload)
    finally:
        if workbook is not None:
            workbook.close()


def _native_file_parts(
    target_dir: Path,
    file_attachments: Any | None,
) -> list[dict[str, str]]:
    """Read staged uploads and encode them as native OpenCode file parts.

    The descriptor is internal to OmicsBase. Only the resulting data URLs are
    passed in the prompt request; local paths are never sent as attachment text
    or as file-part metadata.
    """
    parts: list[dict[str, str]] = []
    root = target_dir.resolve()
    max_native_bytes = max(
        0,
        int(getattr(settings, "opencode_native_attachment_max_bytes", 64_000) or 0),
    )
    native_payload_bytes = 0
    for descriptor in list(file_attachments or []):
        if not isinstance(descriptor, Mapping):
            raise ValueError("Invalid native file attachment descriptor")
        raw_relative = str(descriptor.get("relative_path") or "").strip()
        if not raw_relative:
            raise ValueError("Native file attachment is missing its staged file")
        normalized = raw_relative.replace("\\", "/")
        relative = Path(normalized)
        if relative.is_absolute():
            raise ValueError("Native file attachments must be project-relative")
        source = root / relative
        if source.is_symlink():
            raise ValueError(f"Uploaded attachment is a symlink: {Path(normalized).name}")
        resolved = source.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("Native file attachment escaped the project workspace") from exc
        if not resolved.is_file():
            raise FileNotFoundError(f"Uploaded attachment is unavailable: {Path(normalized).name}")
        filename = Path(str(descriptor.get("filename") or resolved.name).replace("\\", "/")).name
        if not filename or filename in {".", ".."}:
            filename = resolved.name
        source_size = resolved.stat().st_size
        if max_native_bytes and source_size > max_native_bytes:
            logger.info(
                "Keeping large attachment out of OpenCode prompt context: %s (%d bytes)",
                filename,
                source_size,
            )
            continue
        mime = str(descriptor.get("mime") or "").strip()
        if not mime:
            mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        content = resolved.read_bytes()
        if Path(filename).suffix.lower() in _SPREADSHEET_SUFFIXES:
            content = _spreadsheet_text_content(
                resolved,
                max(0, max_native_bytes - native_payload_bytes),
            )
            mime = "text/plain"
        if max_native_bytes and native_payload_bytes + len(content) > max_native_bytes:
            logger.info(
                "Keeping attachment out of OpenCode prompt context after payload cap: %s (%d bytes)",
                filename,
                len(content),
            )
            continue
        native_payload_bytes += len(content)
        parts.append(
            _native_file_part(
                filename=filename,
                mime=mime,
                content=content,
            )
        )
    return parts


async def stream_opencode(
    project_dir: str | Path,
    instruction: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    session_id: str | None = None,
    fresh_session: bool = False,
    chat_mode: str | None = None,
    cancel_check: Callable[[], bool] | None = None,
    question: str | None = None,
    plan: str | None = None,
    notes: str | None = None,
    existing_sources: str | None = None,
    attachments: str | None = None,
    selected_file: str | None = None,
    selected_content: str | None = None,
    selected_content_dirty: bool = False,
    preview_path: str | None = None,
    file_attachments: list[dict[str, Any]] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Relay one workspace turn through a living ``opencode serve`` session.

    The turn is over when OpenCode says so — ``session.idle`` for this session,
    or a session that stays idle across several polls once the turn has started.
    A momentarily non-busy status between tool rounds is not an ending, and
    ``session.error`` arrives *after* ``session.idle``, so the loop drains a
    short window past idle before it reports the outcome.
    """
    import httpx

    from app.services.opencode_server import ensure_server

    target_dir = Path(project_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    model_spec = resolve_model_spec(provider, model)
    user_prompt = compose_user_prompt(
        instruction,
        question=question,
        plan=plan,
        notes=notes,
        existing_sources=existing_sources,
        attachments=attachments,
        selected_file=selected_file,
        selected_content=selected_content,
        selected_content_dirty=selected_content_dirty,
        preview_path=preview_path,
        chat_mode=chat_mode,
    )
    system_prompt = workspace_system_prompt()
    try:
        native_file_parts = _native_file_parts(target_dir, file_attachments)
    except Exception as exc:
        detail = f"OpenCode attachment preparation failed: {exc}"
        yield {"type": "error", "error": detail}
        yield build_final_event(TurnOutcome(), turn_ended=False, relay_errors=[detail])
        return

    try:
        base_url = await ensure_server()
    except Exception as exc:
        detail = f"OpenCode server is unavailable: {exc}"
        yield {"type": "error", "error": detail}
        yield build_final_event(TurnOutcome(), turn_ended=False, relay_errors=[detail])
        return

    auth = _basic_auth()
    timeout = httpx.Timeout(connect=15.0, read=None, write=60.0, pool=15.0)

    streamed_text: list[str] = []
    streamed_reasoning: list[str] = []
    asked_user: dict[str, Any] | None = None
    relay_errors: list[str] = []
    cancelled = False
    turn_started = False
    turn_ended = False
    step_counter = 0
    idle_polls = 0
    startup_polls = 0
    active_session_id = ""
    seen_text: dict[str, str] = {}
    seen_reasoning: dict[str, str] = {}
    message_roles: dict[str, str] = {}
    emitted_tools: set[str] = set()
    prior_message_ids: set[str] = set()
    turn_message_ids: set[str] = set()
    outcome = TurnOutcome()
    cancel_watcher: asyncio.Task[None] | None = None

    async with httpx.AsyncClient(base_url=base_url, auth=auth, timeout=timeout) as client:
        directory = str(target_dir)

        async def resolve_message_role(message_id: str) -> str | None:
            if not message_id:
                return None
            role = message_roles.get(message_id)
            if role:
                return role
            message_roles.update(await load_message_roles(client, active_session_id, directory))
            return message_roles.get(message_id)

        event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        stop_reading = asyncio.Event()
        cancel_signal = asyncio.Event()

        async def read_global_events() -> None:
            """Follow /global/event, reconnecting until the turn is finished."""
            backoff = 0.5
            while not stop_reading.is_set():
                try:
                    async with client.stream("GET", "/global/event") as response:
                        response.raise_for_status()
                        backoff = 0.5
                        data_lines: list[str] = []
                        async for raw_line in response.aiter_lines():
                            if stop_reading.is_set():
                                return
                            line = raw_line.strip()
                            if not line:
                                if not data_lines:
                                    continue
                                try:
                                    payload = json.loads("\n".join(data_lines))
                                except json.JSONDecodeError:
                                    data_lines.clear()
                                    continue
                                data_lines.clear()
                                await event_queue.put(payload)
                                continue
                            if line.startswith("data:"):
                                data_lines.append(line[5:].strip())
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("OpenCode event stream dropped: %s", exc)
                if stop_reading.is_set():
                    return
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 5.0)

        reader = asyncio.create_task(read_global_events())

        async def respond_permission(permission_id: str) -> None:
            response = await client.post(
                f"/session/{active_session_id}/permissions/{permission_id}",
                params={"directory": directory},
                json={"response": "once"},
            )
            if response.status_code >= 400:
                await client.post(
                    f"/permission/{permission_id}/reply",
                    params={"directory": directory},
                    json={"response": "once"},
                )

        async def pause_for_question(request_id: str) -> None:
            """Reject the blocking native call and end this turn cleanly."""
            reject = await client.post(
                f"/question/{request_id}/reject",
                params={"directory": directory},
            )
            if reject.status_code >= 400:
                raise RuntimeError(
                    f"OpenCode question reject failed ({reject.status_code}): {reject.text[:300]}"
                )
            abort = await client.post(
                f"/session/{active_session_id}/abort",
                params={"directory": directory},
            )
            if abort.status_code >= 400:
                raise RuntimeError(
                    f"OpenCode session abort failed ({abort.status_code}): {abort.text[:300]}"
                )

        async def current_session_status() -> dict[str, Any] | None:
            response = await client.get("/session/status", params={"directory": directory})
            if response.status_code != 200:
                return None
            status_map = response.json()
            if not isinstance(status_map, dict):
                return None
            status = status_map.get(active_session_id) or {}
            return status if isinstance(status, dict) else {}

        async def abort_active_session() -> None:
            """Best-effort provider abort used for cancellation and relay failure."""
            if not active_session_id:
                return
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    client.post(
                        f"/session/{active_session_id}/abort",
                        params={"directory": directory},
                    ),
                    timeout=5.0,
                )

        async def watch_for_cancellation() -> None:
            """Abort OpenCode even while no provider event is arriving."""
            while not stop_reading.is_set() and not cancel_signal.is_set():
                try:
                    if cancel_check and cancel_check():
                        cancel_signal.set()
                        await abort_active_session()
                        return
                except Exception as exc:
                    logger.debug("OpenCode cancellation poll failed: %s", exc)
                try:
                    await asyncio.wait_for(cancel_signal.wait(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue

        try:
            await ensure_project_runtime(
                client,
                target_dir,
                model_spec=model_spec,
                provider=provider,
            )
            active_session_id = await ensure_session(
                client,
                target_dir,
                session_id,
                fresh=fresh_session,
            )
            cancel_watcher = asyncio.create_task(
                watch_for_cancellation(),
                name=f"opencode-cancel-watch-{active_session_id}",
            )
            # Expose the external handle so durable orchestration can associate
            # cancellation and telemetry with the provider session.
            yield {"type": "session", "session_id": active_session_id}

            session_messages = await load_messages(client, active_session_id, directory)
            for item in session_messages:
                info = message_info(item)
                message_id = str(info.get("id") or "").strip()
                role = str(info.get("role") or "").strip()
                if message_id:
                    prior_message_ids.add(message_id)
                    if role:
                        message_roles[message_id] = role

            # Keep context-size measurements without persisting or relaying
            # the raw history. The estimate is deliberately provider-neutral
            # (roughly four characters per token) and is diagnostic only.
            history_chars = sum(
                len(json.dumps(item, ensure_ascii=False, separators=(",", ":"), default=str))
                for item in session_messages
            )
            system_prompt_chars = len(system_prompt)
            user_prompt_chars = len(user_prompt)
            attachment_payload_chars = sum(
                len(str(part.get("url") or ""))
                + len(str(part.get("filename") or ""))
                + len(str(part.get("mime") or ""))
                for part in native_file_parts
            )
            total_prompt_chars = system_prompt_chars + user_prompt_chars + attachment_payload_chars
            yield {
                "type": "context_snapshot",
                "session_message_count": len(session_messages),
                "session_history_chars": history_chars,
                "session_history_tokens_estimate": max(0, (history_chars + 3) // 4),
                "system_prompt_chars": system_prompt_chars,
                "prompt_chars": user_prompt_chars,
                "prompt_tokens_estimate": max(0, (user_prompt_chars + 3) // 4),
                "attachment_count": len(native_file_parts),
                "attachment_payload_chars": attachment_payload_chars,
                "estimated_context_tokens": max(0, (history_chars + total_prompt_chars + 3) // 4),
            }

            prompt_task = asyncio.create_task(
                client.post(
                    f"/session/{active_session_id}/prompt_async",
                    params={"directory": directory},
                    json=_prompt_payload(
                        model_spec=model_spec,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        file_parts=native_file_parts,
                    ),
                ),
                name=f"opencode-prompt-{active_session_id}",
            )
            cancel_wait_task = (
                asyncio.create_task(
                    cancel_signal.wait(),
                    name=f"opencode-prompt-cancel-{active_session_id}",
                )
                if cancel_check
                else None
            )
            try:
                if cancel_wait_task is None:
                    prompt_response = await prompt_task
                else:
                    done, _pending = await asyncio.wait(
                        {prompt_task, cancel_wait_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if cancel_wait_task in done and cancel_signal.is_set():
                        prompt_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await prompt_task
                        cancelled = True
                        await abort_active_session()
                        yield {"type": "cancelled"}
                        return
                    prompt_response = await prompt_task
            finally:
                if cancel_wait_task is not None:
                    cancel_wait_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await cancel_wait_task
            if prompt_response.status_code >= 400:
                body = prompt_response.text
                raise RuntimeError(
                    f"OpenCode prompt failed ({prompt_response.status_code}): {body[:500]}"
                )

            drain_until: float | None = None
            loop_clock = asyncio.get_running_loop()
            last_heartbeat_at = loop_clock.time()

            while True:
                if cancel_signal.is_set() or (cancel_check and cancel_check()):
                    cancelled = True
                    cancel_signal.set()
                    await abort_active_session()
                    yield {"type": "cancelled"}
                    break

                if drain_until is not None:
                    wait_for = drain_until - loop_clock.time()
                    if wait_for <= 0:
                        break
                else:
                    wait_for = _POLL_INTERVAL_SECONDS

                try:
                    payload = await asyncio.wait_for(event_queue.get(), timeout=wait_for)
                except asyncio.TimeoutError:
                    if drain_until is not None:
                        break
                    now = loop_clock.time()
                    if now - last_heartbeat_at >= 10.0:
                        last_heartbeat_at = now
                        yield {"type": "heartbeat"}
                    # No events for a while: ask the server whether it is still
                    # working. One idle reading proves nothing — the session is
                    # briefly not busy between tool rounds, and it is idle for a
                    # moment before it picks the prompt up at all.
                    try:
                        status = await asyncio.wait_for(
                            current_session_status(),
                            timeout=5.0,
                        )
                    except Exception as exc:
                        logger.warning("OpenCode status poll failed: %s", exc)
                        continue
                    if status is None:
                        continue
                    if opencode_status_is_active(status):
                        idle_polls = 0
                        turn_started = True
                        continue
                    if not turn_started:
                        startup_polls += 1
                        if startup_polls >= _STARTUP_IDLE_POLLS:
                            break
                        continue
                    idle_polls += 1
                    if idle_polls >= _IDLE_CONFIRM_POLLS:
                        turn_ended = True
                        drain_until = loop_clock.time() + _IDLE_DRAIN_SECONDS
                    continue

                payload = payload.get("payload") if "payload" in payload else payload
                if not isinstance(payload, dict):
                    continue

                event_type = str(payload.get("type") or "")
                properties = payload.get("properties") if isinstance(payload.get("properties"), dict) else (
                    payload.get("data") if isinstance(payload.get("data"), dict) else {}
                )

                if properties.get("sessionID") and properties.get("sessionID") != active_session_id:
                    continue

                if event_type in {"question.asked", "question.v2.asked"}:
                    clarification = clarification_request_from_question_event(properties)
                    request_id = str(properties.get("id") or properties.get("requestID") or "").strip()
                    if clarification is None or not request_id.startswith("que"):
                        raise RuntimeError("OpenCode emitted an invalid question request")
                    await pause_for_question(request_id)
                    asked_user = clarification
                    turn_started = True
                    turn_ended = True
                    yield {
                        "type": "question",
                        "request": clarification,
                    }
                    break

                if event_type in {"permission.asked", "permission.v2.asked"}:
                    permission_id = permission_id_from_event(properties)
                    if permission_id:
                        try:
                            await respond_permission(permission_id)
                        except Exception as exc:
                            logger.warning("Failed to auto-approve OpenCode permission %s: %s", permission_id, exc)
                    continue

                if event_type == "message.updated":
                    info = properties.get("info") if isinstance(properties.get("info"), dict) else {}
                    message_id = str(info.get("id") or "").strip()
                    role = str(info.get("role") or "").strip()
                    if message_id and role:
                        message_roles[message_id] = role
                    if role == "assistant" and message_id and message_id not in prior_message_ids:
                        turn_started = True
                        turn_message_ids.add(message_id)
                        idle_polls = 0
                    continue

                if event_type == "message.part.delta":
                    message_id = str(properties.get("messageID") or "")
                    if message_id in prior_message_ids:
                        continue
                    field_name = str(properties.get("field") or "")
                    delta = str(properties.get("delta") or "")
                    if not delta or field_name not in {"text", "reasoning"}:
                        continue
                    if await resolve_message_role(message_id) != "assistant":
                        continue
                    turn_started = True
                    idle_polls = 0
                    part_id = str(properties.get("partID") or "")
                    seen_map = seen_text if field_name == "text" else seen_reasoning
                    if part_id:
                        seen_map[part_id] = seen_map.get(part_id, "") + delta
                    if field_name == "text":
                        streamed_text.append(delta)
                        yield {"type": "token", "token": delta}
                    else:
                        streamed_reasoning.append(delta)
                        yield {"type": "reasoning_token", "token": delta}
                    continue

                if event_type == "message.part.updated":
                    part = properties.get("part") if isinstance(properties.get("part"), dict) else {}
                    if part.get("sessionID") and part.get("sessionID") != active_session_id:
                        continue
                    message_id = str(part.get("messageID") or "")
                    if message_id and message_id in prior_message_ids:
                        continue
                    idle_polls = 0
                    part_type = str(part.get("type") or "")
                    if part_type == "step-start":
                        step_counter += 1
                        yield {"type": "step_started", "step": step_counter}
                    if part_type in {"text", "reasoning"}:
                        if await resolve_message_role(message_id) != "assistant":
                            continue
                        turn_started = True
                        seen_map = seen_text if part_type == "text" else seen_reasoning
                        delta = part_text_delta(part, seen_map)
                        if not delta:
                            continue
                        if part_type == "text":
                            streamed_text.append(delta)
                            yield {"type": "token", "token": delta}
                        else:
                            streamed_reasoning.append(delta)
                            yield {"type": "reasoning_token", "token": delta}
                        continue

                    if part_type == "tool":
                        turn_started = True
                        tool_part_id = str(part.get("id") or part.get("callID") or "")
                        state = part.get("state") if isinstance(part.get("state"), dict) else {}
                        status = str(state.get("status") or "")
                        emit_key = f"{tool_part_id}:{status}"
                        if emit_key in emitted_tools:
                            continue
                        emitted_tools.add(emit_key)

                    for mapped in map_part_event(part, step_counter):
                        yield mapped
                    continue

                if event_type == "session.updated":
                    # Global SSE also carries unrelated project sessions. The
                    # active session is fixed by ensure_session for this turn.
                    continue

                if event_type == "session.status":
                    status = properties.get("status") if isinstance(properties.get("status"), dict) else {}
                    if opencode_status_is_active(status):
                        turn_started = True
                        idle_polls = 0
                    continue

                if event_type == "session.idle":
                    # Authoritative end of turn. session.error can still follow.
                    turn_ended = True
                    drain_until = loop_clock.time() + _IDLE_DRAIN_SECONDS
                    continue

                if event_type in {"session.error", "session.next.step.failed"}:
                    detail = format_opencode_error(properties.get("error")) or "OpenCode reported an error"
                    if detail not in relay_errors:
                        relay_errors.append(detail)
                    yield {"type": "error", "error": detail}
                    continue

                if event_type == "error":
                    detail = str(properties.get("message") or payload.get("message") or "OpenCode error")
                    if detail not in relay_errors:
                        relay_errors.append(detail)
                    yield {"type": "error", "error": detail}
                    continue

            outcome = TurnOutcome()
            if not cancelled:
                outcome = summarize_turn(
                    await load_messages(client, active_session_id, directory),
                    prior_message_ids=prior_message_ids,
                )
                # Anything the client missed while streaming (a dropped SSE
                # connection, a reasoning block that only landed server-side)
                # is replayed now so the UI ends up with the whole turn.
                if outcome.response and not "".join(streamed_text).strip():
                    yield {"type": "token", "token": outcome.response}
                if outcome.reasoning and not "".join(streamed_reasoning).strip():
                    yield {"type": "reasoning_token", "token": outcome.reasoning}
                for message in outcome.errors:
                    if message not in relay_errors:
                        yield {"type": "error", "error": message}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("OpenCode relay failed for %s", target_dir)
            detail = f"OpenCode relay failed: {exc}"
            if detail not in relay_errors:
                relay_errors.append(detail)
            yield {"type": "error", "error": detail}
        finally:
            # A cancelled consumer or relay exception must not leave the
            # provider session continuing without an OmicsBase owner.
            if active_session_id and not turn_ended and not cancelled:
                await abort_active_session()
            stop_reading.set()
            cancel_signal.set()
            if cancel_watcher is not None:
                cancel_watcher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await cancel_watcher
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader

    if cancelled:
        yield {
            "type": "final",
            "message": "Run cancelled.",
            "ok": True,
            "cancelled": True,
        }
        return

    yield build_final_event(
        outcome,
        turn_ended=turn_ended,
        relay_errors=relay_errors,
        asked_user=asked_user,
        fallback_response="".join(streamed_text),
        fallback_reasoning="".join(streamed_reasoning),
    )

