"""Sanitizer module for prompt and payload data leakage prevention.

Detects and redacts API keys, passwords, authorization tokens, private keys,
and environment secret assignments prior to external LLM provider calls.
"""

from __future__ import annotations

import re
from typing import Any

# Regex patterns matching high-risk secrets and credentials
SECRET_PATTERNS: list[re.Pattern] = [
    # OpenAI & Anthropic API keys
    re.compile(r"sk-(?:proj-|ant-[a-zA-Z0-9-]+)?[a-zA-Z0-9_-]{20,}"),
    # Bearer authorization tokens
    re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.=]{10,}", re.IGNORECASE),
    # Key-value secret assignments (e.g. AWS_SECRET_ACCESS_KEY=..., DATABASE_URL=...)
    re.compile(
        r"\b(?:AWS_SECRET_ACCESS_KEY|AWS_ACCESS_KEY_ID|DATABASE_URL|DATABASE_URI|REDIS_URL|OPENAI_API_KEY|ANTHROPIC_API_KEY|SECRET_KEY|PRIVATE_KEY)\s*=\s*[^\s]+",
        re.IGNORECASE,
    ),
    # Database connection strings with embedded passwords (e.g. postgres://user:pass@host)
    re.compile(r"://[^:]+:([^@]+)@", re.IGNORECASE),
]


def sanitize_text(text: str) -> str:
    """Redact sensitive credentials, tokens, and secrets from input text."""
    if not text:
        return text

    sanitized = text

    # Redact database connection passwords specifically
    sanitized = re.sub(
        r"(://[^:]+:)([^@]+)(@)",
        r"\1[REDACTED_SECRET]\3",
        sanitized,
        flags=re.IGNORECASE,
    )

    # Redact key-value secret assignments
    sanitized = re.sub(
        r"\b(AWS_SECRET_ACCESS_KEY|AWS_ACCESS_KEY_ID|DATABASE_URL|DATABASE_URI|REDIS_URL|OPENAI_API_KEY|ANTHROPIC_API_KEY|SECRET_KEY|PRIVATE_KEY)\s*=\s*[^\s]+",
        r"\1=[REDACTED_SECRET]",
        sanitized,
        flags=re.IGNORECASE,
    )

    # Redact Bearer tokens
    sanitized = re.sub(
        r"(Bearer\s+)[a-zA-Z0-9_\-\.=]{10,}",
        r"\1[REDACTED_SECRET]",
        sanitized,
        flags=re.IGNORECASE,
    )

    # Redact API keys
    sanitized = re.sub(
        r"sk-(?:proj-|ant-[a-zA-Z0-9-]+)?[a-zA-Z0-9_-]{20,}",
        "[REDACTED_SECRET]",
        sanitized,
    )

    return sanitized


def sanitize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sanitize content within a list of message objects."""
    sanitized_messages = []
    for msg in messages:
        cloned = dict(msg)
        if isinstance(cloned.get("content"), str):
            cloned["content"] = sanitize_text(cloned["content"])
        sanitized_messages.append(cloned)
    return sanitized_messages


def truncate_large_content(text: str, max_chars: int = 50_000) -> str:
    """Truncate text exceeding max_chars to prevent context overflow."""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    return f"{truncated}\n... [TRUNCATED: original {len(text)} chars reduced to {max_chars}]"
