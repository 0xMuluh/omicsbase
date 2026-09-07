"""OpenCode prompt/request payload helpers."""

from __future__ import annotations

import base64
import os
import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.config import settings


def _model_payload(model_spec: str) -> dict[str, str]:
    provider_id, _, model_id = str(model_spec or "").partition("/")
    if not provider_id or not model_id:
        raise ValueError(f"Invalid OpenCode model spec: {model_spec!r}")
    return {"providerID": provider_id, "modelID": model_id}


_OPENCODE_VARIANT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def resolve_opencode_variant(value: str | None = None) -> str | None:
    """Return the configured per-turn model variant, if any."""
    raw_value = settings.opencode_agent_variant if value is None else value
    variant = str(raw_value or "").strip()
    if not variant:
        return None
    if not _OPENCODE_VARIANT_PATTERN.fullmatch(variant):
        raise ValueError(
            "OPENCODE_AGENT_VARIANT must be a single variant identifier "
            "containing only letters, numbers, dots, underscores, or hyphens"
        )
    return variant


def prompt_payload(
    *,
    model_spec: str,
    system_prompt: str,
    user_prompt: str,
    variant: str | None = None,
    file_parts: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    parts: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
    for part in file_parts or ():
        if not isinstance(part, Mapping) or part.get("type") != "file":
            raise ValueError("OpenCode file parts must have type=\"file\"")
        mime = str(part.get("mime") or "").strip()
        url = str(part.get("url") or "").strip()
        filename = str(part.get("filename") or "").strip()
        if not mime or not url or not filename:
            raise ValueError("OpenCode file parts require mime, url, and filename")
        # Copy only the native prompt fields. In particular, never put a local
        # source path into the request sent to OpenCode.
        parts.append(
            {
                "type": "file",
                "mime": mime,
                "url": url,
                "filename": filename,
            }
        )
    payload: dict[str, Any] = {
        "agent": "build",
        "model": _model_payload(model_spec),
        "system": system_prompt,
        "tools": {"question": True},
        "parts": parts,
    }
    selected_variant = resolve_opencode_variant(variant)
    if selected_variant:
        payload["variant"] = selected_variant
    return payload



def native_file_part(*, filename: str, mime: str, content: bytes) -> dict[str, str]:
    """Build an OpenCode-native file part from bytes, never from a path.

    OpenCode's prompt API accepts file parts as data URLs. Keeping this
    conversion at the request boundary means the agent receives the uploaded
    bytes while the local project path remains private to the relay.
    """
    safe_filename = str(filename or "").strip()
    selected_mime = str(mime or "").strip()
    if not safe_filename or not selected_mime:
        raise ValueError("Native file parts require a filename and MIME type")
    encoded = base64.b64encode(content).decode("ascii")
    return {
        "type": "file",
        "mime": selected_mime,
        "url": f"data:{selected_mime};base64,{encoded}",
        "filename": safe_filename,
    }


def basic_auth() -> tuple[str, str] | None:
    password = (settings.opencode_server_password or os.environ.get("OPENCODE_SERVER_PASSWORD") or "").strip()
    if not password:
        return None
    username = (settings.opencode_server_username or os.environ.get("OPENCODE_SERVER_USERNAME") or "opencode").strip() or "opencode"
    return username, password

