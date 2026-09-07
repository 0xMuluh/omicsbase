"""Codex app-server adapter for the backend-neutral workspace-agent contract."""

from __future__ import annotations

import asyncio
import os
import shutil
import threading
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.opencode.prompts import compose_user_prompt, workspace_system_prompt

from .codex_app_server import CodexAppServer, CodexAppServerError, ServerMessage
from .contract import AgentEvent, AgentTurnRequest


_SECRET_ENV_NAMES = {
    "API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "DATABASE_URL",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "REDIS_URL",
}
_SECRET_ENV_SUFFIXES = ("_API_KEY", "_PASSWORD", "_SECRET", "_TOKEN")
_APPROVAL_METHODS = {
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "item/permissions/requestApproval",
}


def resolve_codex_bin() -> str:
    configured = str(getattr(settings, "codex_bin", "") or "").strip()
    if configured:
        configured_path = Path(configured).expanduser()
        if configured_path.is_file() and os.access(configured_path, os.X_OK):
            return str(configured_path)
        found = shutil.which(configured)
        if found:
            return found
    found = shutil.which("codex")
    if found:
        return found
    raise RuntimeError(
        "Codex CLI was not found. Install @openai/codex or set CODEX_BIN to its executable."
    )


def _session_path(project_dir: Path) -> Path:
    return project_dir / ".omicsbase" / "codex_session"


def _load_session(project_dir: Path) -> str | None:
    try:
        value = _session_path(project_dir).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def _save_session(project_dir: Path, session_id: str) -> None:
    marker = _session_path(project_dir)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(session_id.strip(), encoding="utf-8")


def _clean_child_env() -> dict[str, str]:
    clean: dict[str, str] = {}
    for name, value in os.environ.items():
        upper = name.upper()
        if upper in _SECRET_ENV_NAMES or upper.endswith(_SECRET_ENV_SUFFIXES):
            continue
        clean[name] = value
    return clean


def _clip(value: Any, limit: int = 2_000) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _tool_name(item: Mapping[str, Any]) -> str:
    item_type = str(item.get("type") or "workspaceAction")
    if item_type == "commandExecution":
        return "shell"
    if item_type == "fileChange":
        return "file_change"
    if item_type in {"mcpToolCall", "dynamicToolCall"}:
        server = str(item.get("server") or item.get("namespace") or "mcp")
        return f"{server}.{str(item.get('tool') or 'tool')}"
    if item_type == "collabAgentToolCall":
        return f"codex.{str(item.get('tool') or 'subagent')}"
    return {
        "webSearch": "web_search",
        "imageView": "view_image",
        "imageGeneration": "image_generation",
    }.get(item_type, item_type)


def _summary(item: Mapping[str, Any]) -> str:
    item_type = str(item.get("type") or "")
    if item_type == "commandExecution":
        command = _clip(item.get("command"), 600)
        exit_code = item.get("exitCode")
        return f"{command} (exit {exit_code})" if exit_code is not None else command
    if item_type == "fileChange":
        changes = item.get("changes") if isinstance(item.get("changes"), list) else []
        rendered = [
            f"{str(change.get('kind') or 'changed')} {str(change.get('path') or '')}".strip()
            for change in changes
            if isinstance(change, Mapping)
        ]
        return _clip(", ".join(rendered) or "Updated workspace files")
    if item_type == "webSearch":
        return _clip(item.get("query") or "Searched the web")
    if item_type in {"mcpToolCall", "dynamicToolCall"}:
        return _clip(item.get("error") or item.get("result") or "Tool call completed")
    if item_type == "collabAgentToolCall":
        return _clip(item.get("prompt") or item.get("status") or "Subagent activity")
    if item_type == "imageView":
        return _clip(item.get("path") or "Viewed an image")
    return _clip(item.get("text") or item.get("result") or item.get("status") or item_type)


def _usage_breakdown(raw: Any) -> dict[str, int]:
    source = raw if isinstance(raw, Mapping) else {}
    return {
        "input_tokens": max(0, int(source.get("inputTokens") or source.get("input_tokens") or 0)),
        "cached_input_tokens": max(0, int(source.get("cachedInputTokens") or source.get("cached_input_tokens") or 0)),
        "output_tokens": max(0, int(source.get("outputTokens") or source.get("output_tokens") or 0)),
        "reasoning_output_tokens": max(0, int(source.get("reasoningOutputTokens") or source.get("reasoning_output_tokens") or 0)),
        "total_tokens": max(0, int(source.get("totalTokens") or source.get("total_tokens") or 0)),
    }


def _subtract_usage(total: Mapping[str, int], baseline: Mapping[str, int]) -> dict[str, int]:
    return {
        key: max(0, int(total.get(key, 0)) - int(baseline.get(key, 0)))
        for key in total
    }


def _question_request(params: Mapping[str, Any]) -> dict[str, Any] | None:
    questions: list[dict[str, Any]] = []
    for raw in params.get("questions") or []:
        if not isinstance(raw, Mapping):
            continue
        question_id = str(raw.get("id") or "").strip()
        prompt = str(raw.get("question") or "").strip()
        if not question_id or not prompt:
            continue
        options = [
            str(option.get("label") or "").strip()
            for option in raw.get("options") or []
            if isinstance(option, Mapping) and str(option.get("label") or "").strip()
        ]
        questions.append(
            {
                "id": question_id,
                "prompt": prompt,
                "options": options,
                "multiple": False,
                "allow_custom": bool(raw.get("isOther")) or not options,
                "depends_on": None,
            }
        )
    if not questions:
        return None
    return {
        "message": "Codex needs your input before it can continue.",
        "questions": questions,
    }


def _approval_mode() -> str:
    value = str(getattr(settings, "codex_approval_mode", "") or "").strip().lower()
    if value not in {"prompt", "auto-accept"}:
        raise RuntimeError(
            "CODEX_APPROVAL_MODE must be explicitly set to prompt or auto-accept"
        )
    return value


class CodexBackend:
    """Run Codex through one persistent app-server process per OmicsBase process."""

    name = "codex"

    def __init__(self) -> None:
        self._runner: CodexAppServer | None = None
        self._runner_key: tuple[str, tuple[tuple[str, str], ...]] | None = None
        self._runner_lock = threading.Lock()

    def _get_runner(self) -> CodexAppServer:
        executable = resolve_codex_bin()
        child_env = _clean_child_env()
        key = (executable, tuple(sorted(child_env.items())))
        with self._runner_lock:
            if self._runner is None or self._runner_key != key:
                self._runner = CodexAppServer(executable, child_env)
                self._runner_key = key
            return self._runner

    async def close(self) -> None:
        with self._runner_lock:
            runner = self._runner
            self._runner = None
            self._runner_key = None
        if runner is not None:
            await runner.close()

    async def _send_approval_decision(
        self,
        runner: CodexAppServer,
        message: ServerMessage,
        decision: str,
    ) -> None:
        if message.request_id is None:
            return
        connection = runner.connection
        if message.method == "item/permissions/requestApproval":
            requested = message.params.get("permissions")
            permissions = (
                dict(requested)
                if decision in {"accept", "acceptForSession"} and isinstance(requested, Mapping)
                else {}
            )
            await runner.call(
                connection.respond(
                    message.request_id,
                    {
                        "permissions": permissions,
                        "scope": "session" if decision == "acceptForSession" else "turn",
                    },
                )
            )
            return
        if decision not in {"accept", "acceptForSession", "decline", "cancel"}:
            raise CodexAppServerError(f"Unsupported Codex approval decision: {decision}")
        await runner.call(
            connection.respond(message.request_id, {"decision": decision})
        )

    async def _respond_to_approval(
        self,
        runner: CodexAppServer,
        message: ServerMessage,
        approval_mode: str,
    ) -> None:
        if message.request_id is None:
            return
        connection = runner.connection
        if approval_mode == "auto-accept":
            if message.method == "item/permissions/requestApproval":
                requested = message.params.get("permissions")
                permissions = dict(requested) if isinstance(requested, Mapping) else {}
                await runner.call(
                    connection.respond(
                        message.request_id,
                        {"permissions": permissions, "scope": "session"},
                    )
                )
            else:
                await runner.call(
                    connection.respond(
                        message.request_id,
                        {"decision": "acceptForSession"},
                    )
                )
            return
        await runner.call(
            connection.respond_error(
                message.request_id,
                code=-32001,
                message="This OmicsBase caller does not support interactive approval",
            )
        )
        raise CodexAppServerError(
            "Codex requested approval from a caller without an approval callback"
        )

    async def _stream(self, request: AgentTurnRequest) -> AsyncIterator[AgentEvent]:
        project_dir = request.project_dir.resolve()
        project_dir.mkdir(parents=True, exist_ok=True)
        prompt = compose_user_prompt(
            request.instruction,
            question=request.question,
            plan=request.plan,
            notes=request.notes,
            existing_sources=request.existing_sources,
            attachments=request.attachments,
            chat_mode=request.chat_mode,
            selected_file=request.selected_file,
            selected_content=request.selected_content,
            selected_content_dirty=request.selected_content_dirty,
            preview_path=request.preview_path,
        )
        try:
            approval_mode = _approval_mode()
            runner = self._get_runner()
        except RuntimeError as exc:
            yield {"type": "error", "error": str(exc)}
            yield {"type": "final", "message": "", "error": str(exc), "ok": False}
            return

        configured_session = request.session.external_id if request.session else None
        session_id = None if request.fresh_session else (
            configured_session or _load_session(project_dir)
        )
        if request.fresh_session:
            _session_path(project_dir).unlink(missing_ok=True)
        model = str(getattr(settings, "codex_model", "") or "").strip()
        effort = str(getattr(settings, "codex_reasoning_effort", "") or "").strip()
        sandbox = str(
            getattr(settings, "codex_sandbox", "workspace-write") or "workspace-write"
        ).strip()
        if request.chat_mode == "discuss":
            sandbox = "read-only"
        if sandbox not in {"read-only", "workspace-write"}:
            raise RuntimeError("CODEX_SANDBOX must be read-only or workspace-write")

        connection = runner.connection
        queue = await runner.call(connection.subscribe(session_id))
        subscribed_thread = session_id
        turn_id: str | None = None
        final_message = ""
        final_by_item: dict[str, str] = {}
        usage_baseline: dict[str, int] | None = None
        usage_total: dict[str, int] | None = None
        asked_user: dict[str, Any] | None = None
        pending_user_input: ServerMessage | None = None
        pending_approval: ServerMessage | None = None
        step_started = False
        interrupted = False
        loop = asyncio.get_running_loop()
        last_heartbeat = loop.time()
        try:
            thread_params: dict[str, Any] = {
                "cwd": str(project_dir),
                "sandbox": sandbox,
                "serviceName": "omicsbase",
                "approvalPolicy": "on-request",
                "developerInstructions": workspace_system_prompt(),
            }
            if model:
                thread_params["model"] = model
            if session_id:
                thread_params.pop("serviceName", None)
                thread_params["threadId"] = session_id
                thread_result = await runner.call(
                    connection.request("thread/resume", thread_params)
                )
            else:
                thread_result = await runner.call(
                    connection.request("thread/start", thread_params)
                )
            thread = (
                thread_result.get("thread")
                if isinstance(thread_result, Mapping)
                else None
            )
            resolved_thread = (
                str(thread.get("id") or "").strip()
                if isinstance(thread, Mapping)
                else ""
            )
            if not resolved_thread:
                raise CodexAppServerError("Codex app-server did not return a thread id")
            if resolved_thread != subscribed_thread:
                await runner.call(
                    connection.move_subscription(
                        queue,
                        old_thread_id=subscribed_thread,
                        new_thread_id=resolved_thread,
                    )
                )
                subscribed_thread = resolved_thread
            session_id = resolved_thread
            _save_session(project_dir, session_id)
            yield {"type": "session", "session_id": session_id}
            yield {
                "type": "context_snapshot",
                "system_prompt_chars": len(workspace_system_prompt()),
                "prompt_chars": len(prompt),
                "prompt_tokens_estimate": (len(prompt) + 3) // 4,
                "attachment_count": len(request.file_attachments),
            }

            turn_params: dict[str, Any] = {
                "threadId": session_id,
                "input": [{"type": "text", "text": prompt}],
                "cwd": str(project_dir),
            }
            if model:
                turn_params["model"] = model
            if effort:
                turn_params["effort"] = effort
            turn_result = await runner.call(
                connection.request("turn/start", turn_params)
            )
            turn = turn_result.get("turn") if isinstance(turn_result, Mapping) else None
            turn_id = (
                str(turn.get("id") or "").strip()
                if isinstance(turn, Mapping)
                else ""
            )
            if not turn_id:
                raise CodexAppServerError("Codex app-server did not return a turn id")

            while True:
                if pending_approval is not None:
                    approval_check = request.options.get("approval_check")
                    if callable(approval_check):
                        response = approval_check(str(pending_approval.request_id))
                        if asyncio.iscoroutine(response):
                            response = await response
                        if isinstance(response, Mapping):
                            decision = str(response.get("decision") or "").strip()
                            if decision:
                                await self._send_approval_decision(
                                    runner, pending_approval, decision
                                )
                                yield {
                                    "type": "approval_resolved",
                                    "request_id": str(pending_approval.request_id),
                                    "decision": decision,
                                }
                                pending_approval = None
                if pending_user_input is not None:
                    input_check = request.options.get("user_input_check")
                    if callable(input_check):
                        response = input_check(str(pending_user_input.request_id))
                        if asyncio.iscoroutine(response):
                            response = await response
                        if isinstance(response, Mapping):
                            answers = response.get("answers")
                            if isinstance(answers, Mapping):
                                await runner.call(
                                    connection.respond(
                                        pending_user_input.request_id,
                                        {"answers": dict(answers)},
                                    )
                                )
                                yield {
                                    "type": "question_resolved",
                                    "request_id": str(pending_user_input.request_id),
                                }
                                pending_user_input = None
                if request.cancel_check and request.cancel_check() and not interrupted:
                    await runner.call(
                        connection.request(
                            "turn/interrupt",
                            {"threadId": session_id, "turnId": turn_id},
                        )
                    )
                    interrupted = True
                try:
                    message = await asyncio.wait_for(
                        runner.call(queue.get()), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    now = loop.time()
                    if now - last_heartbeat >= 10.0:
                        last_heartbeat = now
                        yield {"type": "heartbeat"}
                    continue
                last_heartbeat = loop.time()
                params = message.params
                message_turn = str(params.get("turnId") or "").strip()
                if message_turn and message_turn != turn_id:
                    continue

                if message.request_id is not None:
                    if message.method in _APPROVAL_METHODS:
                        approval_check = request.options.get("approval_check")
                        if approval_mode == "auto-accept":
                            await self._respond_to_approval(runner, message, approval_mode)
                        elif callable(approval_check):
                            pending_approval = message
                            yield {
                                "type": "approval_requested",
                                "request_id": str(message.request_id),
                                "approval_kind": message.method,
                                "reason": _clip(params.get("reason")),
                                "command": _clip(params.get("command"), 1_000),
                                "cwd": _clip(params.get("cwd"), 1_000),
                                "details": dict(params),
                            }
                        else:
                            await self._respond_to_approval(runner, message, approval_mode)
                        continue
                    if message.method == "item/tool/requestUserInput":
                        asked_user = _question_request(params)
                        if asked_user is None:
                            await runner.call(
                                connection.respond_error(
                                    message.request_id,
                                    code=-32602,
                                    message="Codex returned an invalid user-input request",
                                )
                            )
                            raise CodexAppServerError(
                                "Codex returned an invalid user-input request"
                            )
                        input_check = request.options.get("user_input_check")
                        yield {
                            "type": "question",
                            "request": asked_user,
                            "request_id": str(message.request_id),
                            "live": callable(input_check),
                        }
                        if callable(input_check):
                            pending_user_input = message
                        else:
                            pending_user_input = message
                            await runner.call(
                                connection.request(
                                    "turn/interrupt",
                                    {"threadId": session_id, "turnId": turn_id},
                                )
                            )
                        continue
                    await runner.call(
                        connection.respond_error(
                            message.request_id,
                            code=-32601,
                            message=f"OmicsBase does not implement {message.method}",
                        )
                    )
                    continue

                method = message.method
                if method == "connection/error":
                    raise CodexAppServerError(
                        str(
                            params.get("message")
                            or "Codex app-server connection failed"
                        )
                    )
                if method == "turn/started":
                    if not step_started:
                        step_started = True
                        yield {"type": "step_started", "step": 1}
                    continue
                if method == "item/agentMessage/delta":
                    delta = str(params.get("delta") or "")
                    item_id = str(params.get("itemId") or "")
                    if delta:
                        final_by_item[item_id] = final_by_item.get(item_id, "") + delta
                        yield {"type": "token", "token": delta}
                    continue
                if method in {"item/started", "item/completed"}:
                    item = (
                        params.get("item")
                        if isinstance(params.get("item"), Mapping)
                        else {}
                    )
                    item_type = str(item.get("type") or "")
                    item_id = str(item.get("id") or f"{item_type}-1")
                    if item_type == "agentMessage" and method == "item/completed":
                        text = str(item.get("text") or final_by_item.get(item_id) or "")
                        phase = str(item.get("phase") or "")
                        if text and phase in {"", "final_answer"}:
                            final_message = text
                        continue
                    tool_types = {
                        "commandExecution",
                        "fileChange",
                        "mcpToolCall",
                        "dynamicToolCall",
                        "collabAgentToolCall",
                        "webSearch",
                        "imageView",
                        "imageGeneration",
                    }
                    if item_type not in tool_types:
                        continue
                    tool = _tool_name(item)
                    if method == "item/started":
                        yield {
                            "type": "tool_started",
                            "tool": tool,
                            "tool_call_id": item_id,
                            "reason": _summary(item),
                            "step": 1,
                        }
                    else:
                        status = str(item.get("status") or "").lower()
                        failed = status in {"failed", "error", "declined"} or bool(
                            item.get("error")
                        )
                        yield {
                            "type": "tool_completed",
                            "tool": tool,
                            "tool_call_id": item_id,
                            "status": "error" if failed else "ok",
                            "summary": _summary(item),
                            "step": 1,
                        }
                    continue
                if method == "thread/tokenUsage/updated":
                    token_usage = params.get("tokenUsage")
                    if not isinstance(token_usage, Mapping):
                        continue
                    latest_total = _usage_breakdown(token_usage.get("total"))
                    latest_last = _usage_breakdown(token_usage.get("last"))
                    if usage_baseline is None:
                        usage_baseline = _subtract_usage(latest_total, latest_last)
                    usage_total = latest_total
                    continue
                if method == "turn/completed":
                    completed_turn = (
                        params.get("turn")
                        if isinstance(params.get("turn"), Mapping)
                        else {}
                    )
                    status = str(completed_turn.get("status") or "")
                    usage = _subtract_usage(usage_total or {}, usage_baseline or {})
                    yield {"type": "step_completed", "step": 1, "tokens": usage}
                    if interrupted:
                        yield {"type": "cancelled"}
                        return
                    if asked_user is not None and pending_user_input is not None:
                        yield {
                            "type": "final",
                            "message": final_message,
                            "ok": True,
                            "awaiting_answer": asked_user,
                        }
                        return
                    if status == "interrupted":
                        yield {"type": "cancelled"}
                        return
                    if status != "completed":
                        error = completed_turn.get("error")
                        detail = _clip(
                            error.get("message")
                            if isinstance(error, Mapping)
                            else error
                        ) or f"Codex turn ended with status {status or 'unknown'}"
                        yield {"type": "error", "error": detail}
                        yield {
                            "type": "final",
                            "message": final_message,
                            "error": detail,
                            "ok": False,
                        }
                        return
                    yield {"type": "final", "message": final_message, "ok": True}
                    return
        except asyncio.CancelledError:
            if session_id and turn_id:
                try:
                    await asyncio.shield(
                        runner.call(
                            connection.request(
                                "turn/interrupt",
                                {"threadId": session_id, "turnId": turn_id},
                            )
                        )
                    )
                except Exception:
                    pass
            raise
        except Exception as exc:
            detail = _clip(exc) or "Codex app-server failed"
            yield {"type": "error", "error": detail}
            yield {
                "type": "final",
                "message": final_message,
                "error": detail,
                "ok": False,
            }
        finally:
            await runner.call(connection.unsubscribe(queue, subscribed_thread))

    def stream_turn(self, request: AgentTurnRequest) -> AsyncIterator[AgentEvent]:
        return self._stream(request)


__all__ = ["CodexBackend", "resolve_codex_bin"]
