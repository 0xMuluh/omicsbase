"""Fast-intent path: answer clear, simple questions without the tool loop.

A heuristic decides whether a message is a plain factual/definitional
question that needs no workspace, notebook, or R access. When it is,
the lens answers directly with a faster model and a short token cap,
instead of running the full streaming tool-calling loop (which carries
tool definitions, live context, and a slow reasoning model).
"""

from __future__ import annotations

import re
from typing import Any, AsyncIterator

from app.config import settings
from app.services.llm import stream_llm_text

# Words or fragments that imply the user expects work with data, code, or
# the product surfaces. Any hit routes the message back to the full loop.
_TOOL_HINTS = (
    "data", "dataset", "file", "files", "upload", "attach", "import", "csv", "tsv",
    "qza", "qmd", "excel", "rds", "table", "columns", "cell", "cells", "code", "r code",
    "plot", "graph", "chart", "figure", "build", "report", "render", "run", "execute",
    "workspace", "notebook", "project", "recipe", "package", "install", "library(",
    "compare", "groups", "group", "grouping", "sample", "samples", "analysis", "analyze",
    "edit", "fix", "save", "write", "output", "result", "results",
    "my data", "our data", "the data",
)

# Action phrasing implies computation or a task on this system.
_ACTION_VERB = re.compile(
    r"\b(calculate|compute|run|execute|plot|build|make|create|fit|"
    r"summarise|summarize|estimate|convert|merge|join|filter|sort|scale|"
    r"normalise|normalize)\b",
    re.IGNORECASE,
)

_QUESTION_LEAD = re.compile(
    r"^(what|what's|whats|why|how|how's|hows|define|explain|when|where|which|"
    r"who|is|are|can|could|should|does|do|would|tell|describe|difference)\b",
    re.IGNORECASE,
)

FAST_PATH_SYSTEM = (
    "You are OmicsBase, answering a quick scientific question directly. "
    "Answer in 1-3 concise sentences with plain markdown. Do not invent facts, "
    "files, methods, or numbers. If the question cannot be answered briefly and "
    "correctly, say what would be needed instead."
)


def is_simple_question(message: str) -> bool:
    """High-precision heuristic: plain factual question, no tool implied."""
    if not settings.fast_path_enabled:
        return False
    text = (message or "").strip()
    if not text or len(text) > 200:
        return False
    lowered = text.lower()
    if any(hint in lowered for hint in _TOOL_HINTS):
        return False
    if _ACTION_VERB.search(lowered):
        return False
    if not (text.endswith("?") or _QUESTION_LEAD.match(lowered)):
        return False
    # Anything referencing an attached/known entity by filename-like token.
    if re.search(r"[\w-]+\.(r|qmd|csv|tsv|xlsx|txt|qza|biom)$", lowered):
        return False
    return True


def fast_path_model() -> str | None:
    """Resolve the fast model for the configured provider."""
    if settings.fast_path_model:
        return settings.fast_path_model
    provider = settings.llm_provider.lower()
    defaults = {
        "qwen": "qwen-plus",
        "groq": "llama-3.3-70b-versatile",
        "gemini": "gemini-2.0-flash",
        "openrouter": "anthropic/claude-3.5-haiku",
        "anthropic": "claude-haiku-4-5",
        "openai": "gpt-4o-mini",
        "deepseek": "deepseek-chat",
        "grok": "grok-2-latest",
        "xai": "grok-2-latest",
        "ollama": None,
    }
    return defaults.get(provider)


async def stream_simple_answer(message: str) -> AsyncIterator[dict[str, Any]]:
    """Stream a direct answer for a simple question as agent events."""
    from app.services.llm import resolve_target

    provider_override, model_override = resolve_target("fast")
    model = model_override or fast_path_model()
    yield {"type": "status", "status": "thinking", "message": "Answering directly", "fast": True}
    chunks: list[str] = []
    async for chunk in stream_llm_text(
        system_prompt=FAST_PATH_SYSTEM,
        user_prompt=message,
        max_tokens=400,
        model_override=model,
        provider_override=provider_override,
    ):
        chunks.append(chunk)
        yield {"type": "token", "token": chunk}
    answer = "".join(chunks).strip() or "I could not produce a grounded answer."
    yield {"type": "final", "message": answer, "fast": True}
