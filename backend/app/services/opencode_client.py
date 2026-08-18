"""OpenCode coding agent client adapter for OmicsBase.

Executes OpenCode in headless mode with structured JSON streaming output,
routing project tasks, template adaptation, and Quarto report generation
directly to the native coding agent with BYOK support.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, AsyncIterator

from app.config import settings

logger = logging.getLogger(__name__)


def resolve_opencode_bin() -> str:
    """Find the opencode executable binary."""
    configured = (settings.opencode_bin or "").strip()
    if configured and Path(configured).is_file() and os.access(configured, os.X_OK):
        return configured
    found = shutil.which("opencode")
    if found:
        return found
    user_opencode = Path.home() / ".opencode" / "bin" / "opencode"
    if user_opencode.is_file() and os.access(user_opencode, os.X_OK):
        return str(user_opencode)
    raise RuntimeError("OpenCode binary not found. Please ensure opencode is installed.")


def build_opencode_env(
    *,
    provider: str | None = None,
    api_key: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Assemble environment variables with user-provided or configured BYOK keys."""
    env = os.environ.copy()

    # Pass through configured backend keys
    if settings.anthropic_api_key:
        env["ANTHROPIC_API_KEY"] = settings.anthropic_api_key
    if settings.openai_api_key:
        env["OPENAI_API_KEY"] = settings.openai_api_key
    if settings.gemini_api_key:
        # opencode's Google provider reads GOOGLE_GENERATIVE_AI_API_KEY
        # (models.dev convention); GEMINI_API_KEY is not sufficient.
        env["GOOGLE_GENERATIVE_AI_API_KEY"] = settings.gemini_api_key
        env["GEMINI_API_KEY"] = settings.gemini_api_key
    if settings.openrouter_api_key:
        env["OPENROUTER_API_KEY"] = settings.openrouter_api_key
    if settings.groq_api_key:
        env["GROQ_API_KEY"] = settings.groq_api_key
    if settings.dashscope_api_key:
        env["DASHSCOPE_API_KEY"] = settings.dashscope_api_key

    # Override with per-request BYOK if provided
    active_provider = (provider or settings.llm_provider or "openai").lower().strip()
    if api_key and api_key.strip():
        if active_provider == "anthropic":
            env["ANTHROPIC_API_KEY"] = api_key.strip()
        elif active_provider in {"openai", "chatgpt"}:
            env["OPENAI_API_KEY"] = api_key.strip()
        elif active_provider in {"gemini", "google"}:
            env["GOOGLE_GENERATIVE_AI_API_KEY"] = api_key.strip()
            env["GEMINI_API_KEY"] = api_key.strip()
        elif active_provider in {"openrouter", "orcarouter"}:
            env["OPENROUTER_API_KEY"] = api_key.strip()
        elif active_provider == "groq":
            env["GROQ_API_KEY"] = api_key.strip()
        elif active_provider in {"dashscope", "qwen"}:
            env["DASHSCOPE_API_KEY"] = api_key.strip()

    if extra_env:
        env.update(extra_env)

    return env


def resolve_model_spec(provider: str | None = None, model: str | None = None) -> str:
    """Format model specifier for OpenCode (provider/model)."""
    p = (provider or settings.llm_provider or "openai").lower().strip()
    m = (model or settings.llm_model or "").strip()

    # Normalize provider naming for OpenCode
    if p in {"gemini", "google"}:
        provider_prefix = "google"
        if not m:
            m = "gemini-2.5-flash"
    elif p == "anthropic":
        provider_prefix = "anthropic"
        if not m or "claude" not in m:
            m = "claude-3-7-sonnet-20250219"
    elif p == "openai":
        provider_prefix = "openai"
        if not m:
            m = "gpt-4o"
    elif p in {"openrouter", "orcarouter"}:
        provider_prefix = "openrouter"
    elif p == "groq":
        provider_prefix = "groq"
    else:
        provider_prefix = p

    if "/" in m:
        return m
    return f"{provider_prefix}/{m}" if m else "openai/gpt-4o"


def opencode_mcp_config(project_dir: str | Path) -> str:
    """Inline opencode config exposing the OmicsBase contract MCP server."""
    target = Path(project_dir).resolve()
    return json.dumps({
        "mcp": {
            "omicsbase": {
                "type": "local",
                "command": ["python3", "-m", "app.services.omicsbase_mcp_server"],
                "cwd": "/app",
                "environment": {"OMICSBASE_PROJECT_DIR": str(target)},
                "enabled": True,
            }
        }
    })


async def stream_opencode(
    project_dir: str | Path,
    instruction: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    session_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream events from an OpenCode execution against a project directory."""
    opencode_bin = resolve_opencode_bin()
    target_dir = Path(project_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    model_spec = resolve_model_spec(provider, model)
    env = build_opencode_env(provider=provider, api_key=api_key)

    # Expose OmicsBase contract tools (set_plan, ask_user) to the opencode
    # agent as a stdio MCP server. Without this the agent can only write a
    # plan as text and never commit it to the project.
    env["OPENCODE_CONFIG_CONTENT"] = opencode_mcp_config(target_dir)

    full_prompt = instruction.strip()

    cmd = [
        opencode_bin,
        "run",
        "--format", "json",
        "--thinking",
        "--auto",
        "--dir", str(target_dir),
        "--model", model_spec,
    ]
    if session_id:
        cmd.extend(["--session", session_id])

    cmd.append(full_prompt)

    logger.info("Executing OpenCode: %s (cwd=%s)", " ".join(cmd[:6]), target_dir)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(target_dir),
        env=env,
    )

    step_counter = 0

    if proc.stdout is not None:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            line_str = line.decode("utf-8", errors="replace").strip()
            if not line_str:
                continue

            try:
                event = json.loads(line_str)
            except json.JSONDecodeError:
                # Raw text fallback
                yield {"type": "token", "token": line_str + "\n"}
                continue

            event_type = event.get("type")
            part = event.get("part", {})

            if event_type == "step_start":
                step_counter += 1
                yield {
                    "type": "step_start",
                    "step": step_counter,
                    "session_id": event.get("sessionID"),
                }

            elif event_type == "tool_use":
                tool_name = part.get("tool") or "tool"
                tool_state = part.get("state", {})
                tool_input = tool_state.get("input", {})
                tool_output = tool_state.get("output") or ""
                summary = tool_state.get("title") or f"{tool_name} executed"

                yield {
                    "type": "tool_started",
                    "tool": tool_name,
                    "reason": summary,
                    "step": step_counter,
                    "input": tool_input,
                }
                yield {
                    "type": "action_event",
                    "event": {
                        "id": part.get("id") or f"tool-{step_counter}",
                        "kind": "action",
                        "status": "ok" if tool_state.get("status") == "completed" else "error",
                        "title": tool_name,
                        "summary": summary,
                        "output": str(tool_output)[:1000],
                    },
                }

            elif event_type == "text":
                text = part.get("text", "")
                if text:
                    yield {"type": "token", "token": text}

            elif event_type == "step_finish":
                yield {
                    "type": "step_completed",
                    "step": step_counter,
                    "tokens": part.get("tokens"),
                    "cost": part.get("cost"),
                }

            elif event_type == "error":
                err_data = event.get("error", {})
                err_msg = err_data.get("data", {}).get("message") or err_data.get("name") or "OpenCode execution error"
                yield {"type": "error", "error": err_msg}

    stderr_output = b""
    if proc.stderr is not None:
        stderr_output = await proc.stderr.read()

    await proc.wait()

    if proc.returncode != 0 and stderr_output:
        logger.warning("OpenCode exited with code %s: %s", proc.returncode, stderr_output.decode("utf-8", errors="replace"))
        yield {
            "type": "error",
            "error": f"OpenCode exited with code {proc.returncode}: {stderr_output.decode('utf-8', errors='replace')[:500]}",
        }
