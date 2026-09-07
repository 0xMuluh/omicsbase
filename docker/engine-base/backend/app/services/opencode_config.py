"""OpenCode provider, runtime and session configuration.

This module owns deployment mechanics. The streaming relay and provider
adapter consume it directly.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.providers import api_key_for, base_url_for, default_model_for


_OPENCODE_PROVIDER_KEYS = (
    "ORCAROUTER_API_KEY",
    "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GROQ_API_KEY",
    "DASHSCOPE_API_KEY",
    "QWEN_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_GENERATIVE_AI_API_KEY",
    "GOOGLE_API_KEY",
    "XAI_API_KEY",
    "GROK_API_KEY",
    "DEEPSEEK_API_KEY",
    "ZAI_API_KEY",
    "ZENMUX_API_KEY",
    "GMI_API_KEY",
    "BAI_API_KEY",
    "OPENCODE_API_KEY",
)

_ROUTER_PROVIDER_IDS = ("orcarouter", "openrouter")

# OpenCode has a built-in catalog entry for vendor model ids such as
# ``MiniMaxAI/MiniMax-M3``. A custom GMI model is registered under a
# slash-free alias while the original id is retained for the upstream call.
_GMI_MODEL_ALIAS_PREFIX = "gmi-b64-"

_OPCODE_PROVIDERS: dict[str, dict[str, str]] = {
    "qwen": {"opencode": "openai", "key": "OPENAI_API_KEY"},
    "dashscope": {"opencode": "openai", "key": "OPENAI_API_KEY"},
    "deepseek": {"opencode": "openai", "key": "OPENAI_API_KEY"},
    "grok": {"opencode": "openai", "key": "OPENAI_API_KEY"},
    "xai": {"opencode": "openai", "key": "OPENAI_API_KEY"},
    "orcarouter": {"opencode": "openai", "key": "OPENAI_API_KEY"},
    "zai": {"opencode": "openai", "key": "OPENAI_API_KEY"},
    "zenmux": {"opencode": "openai", "key": "OPENAI_API_KEY"},
    "gmicloud": {
        "opencode": "gmicloud",
        "key": "OPENAI_API_KEY",
        "npm": "@ai-sdk/openai-compatible",
    },
    "bai": {
        "opencode": "bai",
        "key": "OPENAI_API_KEY",
        "npm": "@ai-sdk/openai-compatible",
    },
    "opencode": {"opencode": "opencode", "key": "OPENCODE_API_KEY"},
    "gemini": {"opencode": "google", "key": "GOOGLE_GENERATIVE_AI_API_KEY"},
    "google": {"opencode": "google", "key": "GOOGLE_GENERATIVE_AI_API_KEY"},
    "anthropic": {"opencode": "anthropic", "key": "ANTHROPIC_API_KEY"},
    "openai": {"opencode": "openai", "key": "OPENAI_API_KEY"},
    "openrouter": {"opencode": "openrouter", "key": "OPENROUTER_API_KEY"},
    "groq": {"opencode": "groq", "key": "GROQ_API_KEY"},
}


def resolve_opencode_bin() -> str:
    """Find the OpenCode executable binary."""
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


def _active_provider_id(provider: str | None = None) -> str:
    return (provider or settings.llm_provider or "openai").lower().strip()


def _gmicloud_model_alias(model_id: str) -> str:
    """Return a slash-free OpenCode key for a GMI upstream model id."""
    value = str(model_id or "").strip()
    if value.startswith(_GMI_MODEL_ALIAS_PREFIX):
        try:
            _decode_gmicloud_model_alias(value)
            return value
        except (ValueError, UnicodeDecodeError):
            pass
    encoded = base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{_GMI_MODEL_ALIAS_PREFIX}{encoded}"


def _decode_gmicloud_model_alias(model_id: str) -> str:
    """Recover the upstream model id encoded in a GMI OpenCode key."""
    value = str(model_id or "").strip()
    if not value.startswith(_GMI_MODEL_ALIAS_PREFIX):
        return value
    encoded = value[len(_GMI_MODEL_ALIAS_PREFIX):]
    if not encoded:
        raise ValueError("empty GMI model alias")
    padded = encoded + ("=" * (-len(encoded) % 4))
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeError, base64.binascii.Error) as exc:
        raise ValueError("invalid GMI model alias") from exc
    if not decoded:
        raise ValueError("empty GMI model alias")
    return decoded


def _provider_env(provider: str, api_key: str | None = None) -> dict[str, str]:
    active = _active_provider_id(provider)
    entry = _OPCODE_PROVIDERS.get(active)
    if entry is None:
        return {}
    value = (api_key or "").strip() or api_key_for(active) or ""
    if not value:
        return {}
    return {entry["key"]: value}


def build_opencode_env(
    *,
    provider: str | None = None,
    api_key: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Assemble an environment containing only the active provider key."""
    env = os.environ.copy()
    for name in _OPENCODE_PROVIDER_KEYS:
        env.pop(name, None)
    env.update(_provider_env(_active_provider_id(provider), api_key))
    if extra_env:
        env.update(extra_env)
    return env


def resolve_model_spec(provider: str | None = None, model: str | None = None) -> str:
    """Format an OpenCode ``provider/model`` specifier."""
    active = _active_provider_id(provider)
    selected_model = (model or settings.llm_model or "").strip()
    entry = _OPCODE_PROVIDERS.get(active)
    provider_prefix = entry["opencode"] if entry else active
    if active == "gemini" and not selected_model:
        selected_model = "gemini-3.6-flash"
    elif active == "anthropic" and "claude" not in selected_model:
        selected_model = "claude-3-7-sonnet-20250219"
    elif active == "openai" and not selected_model:
        selected_model = "gpt-4o"
    elif active == "zenmux" and not selected_model:
        selected_model = "gpt-4o"
    elif active == "gmicloud":
        if not selected_model or "claude" in selected_model.lower():
            selected_model = default_model_for(active, selected_model)
        for prefix in ("gmicloud/", "openai/"):
            if selected_model.startswith(prefix):
                selected_model = selected_model[len(prefix):]
                break
        return f"{provider_prefix}/{_gmicloud_model_alias(selected_model)}"
    elif active == "bai" and not selected_model:
        selected_model = "deepseek-v4-flash"
    if "/" in selected_model:
        if selected_model.startswith(f"{provider_prefix}/"):
            return selected_model
        return f"{provider_prefix}/{selected_model}"
    return f"{provider_prefix}/{selected_model}" if selected_model else "openai/gpt-4o"


def opencode_session_path(project_dir: str | Path) -> Path:
    return Path(project_dir).resolve() / ".omicsbase" / "opencode_session"


def load_opencode_session(project_dir: str | Path) -> str | None:
    path = opencode_session_path(project_dir)
    if not path.is_file():
        return None
    session_id = path.read_text(encoding="utf-8").strip()
    return session_id or None


def save_opencode_session(project_dir: str | Path, session_id: str) -> None:
    path = opencode_session_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(session_id.strip(), encoding="utf-8")


def clear_opencode_session(project_dir: str | Path) -> None:
    opencode_session_path(project_dir).unlink(missing_ok=True)


def _workspace_mcp_config(project_dir: str | Path) -> dict[str, Any]:
    """Return the local MCP bridge used by workspace OpenCode sessions.

    The MCP process uses the mounted application source, then asks the backend
    for knowledge excerpts over its internal HTTP endpoint. It never receives
    database credentials or a knowledge-data mount. Using the active Python
    interpreter and an explicit application path keeps the stdio server
    importable from a project cwd.
    """
    app_root = Path(__file__).resolve().parents[2]
    environment = {
        "OMICSBASE_PROJECT_DIR": str(Path(project_dir).resolve()),
        "PYTHONPATH": str(app_root),
        # Workspace agents receive only the read-only knowledge capability.
        # Clarification questions continue through OpenCode native events.
        "OMICSBASE_MCP_SCOPE": "knowledge",
    }
    configured_api_url = str(os.environ.get("OMICSBASE_API_URL") or "").strip()
    if configured_api_url:
        environment["OMICSBASE_API_URL"] = configured_api_url
    elif str(project_dir).startswith("/app/projects/"):
        # The Docker OpenCode service reaches the backend by its compose
        # service name. This supports containers created before the URL entry.
        environment["OMICSBASE_API_URL"] = "http://backend:8000"
    return {
        "type": "local",
        "command": [sys.executable, "-m", "app.services.omicsbase_mcp_server"],
        "environment": environment,
        # The backend may initialize its local embedding model on the first
        # call; keep the MCP request bounded but practical.
        "timeout": 30_000,
    }


def opencode_runtime_config(
    project_dir: str | Path,
    *,
    model_spec: str,
    provider: str | None = None,
) -> str:
    """Build the per-project OpenCode runtime configuration document."""
    spec = (model_spec or "").strip()
    provider_id, _, model_id = spec.partition("/")
    active = _active_provider_id(provider)
    if active == "gmicloud" and model_id:
        # Normalize legacy openai-prefixed GMI specs as well as the new form.
        model_id = _gmicloud_model_alias(model_id)
        provider_id = _OPCODE_PROVIDERS[active]["opencode"]
        spec = f"{provider_id}/{model_id}"
    disabled = [name for name in _ROUTER_PROVIDER_IDS if name != active]
    config: dict[str, Any] = {
        "model": spec,
        "disabled_providers": disabled,
        "mcp": {"omicsbase": _workspace_mcp_config(project_dir)},
    }
    if provider_id and model_id and active != "opencode":
        upstream_model_id = (
            _decode_gmicloud_model_alias(model_id) if active == "gmicloud" else model_id
        )
        model_config: dict[str, Any] = {
            "id": upstream_model_id,
            "name": upstream_model_id,
            "tool_call": True,
        }
        provider_config: dict[str, Any] = {"models": {model_id: model_config}}
        entry = _OPCODE_PROVIDERS.get(active)
        if entry is not None and (
            entry["opencode"] == "openai" or entry.get("npm") == "@ai-sdk/openai-compatible"
        ):
            if entry.get("npm"):
                provider_config["npm"] = entry["npm"]
            base_url = base_url_for(active)
            options: dict[str, str] = {}
            if base_url:
                options["baseURL"] = base_url
            if base_url and ("dashscope" in base_url.lower() or "aliyun" in base_url.lower()):
                model_config["headers"] = {"x-dashscope-session-cache": "enable"}
            if options:
                provider_config["options"] = options
        config["provider"] = {provider_id: provider_config}
    return json.dumps(config)


def opencode_mcp_config(project_dir: str | Path, *, model_spec: str = "") -> str:
    return opencode_runtime_config(project_dir, model_spec=model_spec or resolve_model_spec())

