"""Shared, ownership-safe titles for projects and note conversations."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy.orm import Session

from app.config import settings
from app.models.notes import NoteThread
from app.models.project import Project
from app.services.llm import call_llm, resolve_target

logger = logging.getLogger(__name__)

DEFAULT_PROJECT_TITLE = "New project"
DEFAULT_NOTE_TITLE = "Untitled note"
MAX_GENERATED_TITLE_CHARS = 80


def clean_generated_title(value: str) -> str:
    """Normalize a title response without damaging scientific capitalization."""
    first_line = str(value or "").strip().splitlines()[0] if str(value or "").strip() else ""
    cleaned = re.sub(r"\s+", " ", first_line).strip().strip("`*_\"'").strip()
    cleaned = cleaned.rstrip(".!?:;,—-").strip()
    if len(cleaned) > MAX_GENERATED_TITLE_CHARS:
        cleaned = cleaned[:MAX_GENERATED_TITLE_CHARS].rsplit(" ", 1)[0].strip()
    return cleaned


def title_context(*values: str | None, file_names: Iterable[str] = ()) -> str:
    """Build compact context for the title model from durable user inputs."""
    parts = [re.sub(r"\s+", " ", str(value or "")).strip() for value in values]
    parts = [part for part in parts if part]
    names = [str(name or "").strip() for name in file_names if str(name or "").strip()]
    if names:
        parts.append("Uploaded files: " + ", ".join(names[:20]))
    return "\n".join(parts)[:2_000]


def fallback_title(user_intent: str) -> str:
    """Produce a bounded readable title when the title provider is unavailable."""
    text = re.sub(r"\s+", " ", str(user_intent or "")).strip()
    text = re.sub(
        r"^(?:please\s+|can you\s+|could you\s+|would you\s+|help me\s+|i want to\s+)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    transformations = (
        (r"^which\s+(.+?)\s+should\s+(?:i|we)\s+use\??$", r"Choosing \1"),
        (r"^what\s+(?:is|are)\s+(.+?)\??$", r"Understanding \1"),
        (r"^how\s+(?:do|can|should)\s+(?:i|we)\s+(.+?)\??$", r"\1"),
        (r"^how\s+to\s+(.+?)\??$", r"\1"),
        (r"^(?:tell me about|show me an example of)\s+(.+?)\??$", r"\1"),
        (
            r"^compare\s+(.+?)\s+between\s+.+?\s+and\s+.+?\s+in\s+(.+?)\.?$",
            r"\1 in \2",
        ),
        (r"^compare\s+(.+?)\.?$", r"\1 comparison"),
        (r"^analy[sz]e\s+(.+?)\.?$", r"\1 analysis"),
    )
    for pattern, replacement in transformations:
        transformed = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        if transformed != text:
            text = transformed
            break
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9+._/-]*", text)[:7]
    if not words:
        return "Scientific Analysis"
    stop_words = {
        "a", "an", "and", "as", "at", "between", "by", "for", "in",
        "of", "on", "or", "the", "to", "vs", "with",
    }
    rendered = []
    for index, word in enumerate(words):
        if any(character.isupper() for character in word[1:]) or any(character.isdigit() for character in word):
            rendered.append(word)
        elif index > 0 and word.lower() in stop_words:
            rendered.append(word.lower())
        else:
            rendered.append(word[0].upper() + word[1:])
    return clean_generated_title(" ".join(rendered))


async def generate_title(*, kind: str, user_intent: str, context: str = "") -> str:
    """Generate one short sidebar title using the configured title model."""
    normalized_kind = "project" if str(kind).strip().lower() == "project" else "note"
    system_prompt = (
        "Generate a concise sidebar title for scientific work. Return only the title. "
        "Use 3 to 7 words, preserve scientific capitalization such as RNA-seq, and do not "
        "use quotes or sentence-ending punctuation. For a project, identify the study and "
        "the requested work using a distinguishing assay, cohort, organism, or comparison "
        "when available. For a note, summarize the user's conversational intent. Avoid vague "
        "titles such as New Project, Study Analysis, General Question, or Untitled Note when "
        "the input provides a more specific subject."
    )
    prompt = f"Object: {normalized_kind}\nUser intent: {str(user_intent or '').strip()[:1_200]}"
    if context.strip():
        prompt += f"\nContext:\n{context.strip()[:2_000]}"
    title_provider, title_model = resolve_target("title")
    try:
        raw = await asyncio.wait_for(
            call_llm(
                system_prompt=system_prompt,
                user_prompt=prompt,
                max_tokens=32,
                model_override=title_model,
                provider_override=title_provider,
            ),
            timeout=max(0.25, float(settings.title_generation_timeout_seconds or 2.0)),
        )
        cleaned = clean_generated_title(raw)
        if cleaned:
            return cleaned
    except Exception as exc:
        logger.warning("Title model unavailable; using local fallback: %s", exc)
    return fallback_title(user_intent)


def claim_project_auto_title(
    db: Session,
    *,
    project_id: str,
    expected_name: str,
    proposed_name: str,
) -> str | None:
    """Apply a generated project title only while its default name is unchanged."""
    clean = clean_generated_title(proposed_name)[:255]
    if not clean or clean == expected_name:
        return None
    updated = (
        db.query(Project)
        .filter(
            Project.id == str(project_id),
            Project.name_source == "default",
            Project.name == expected_name,
        )
        .update(
            {Project.name: clean, Project.name_source: "auto"},
            synchronize_session=False,
        )
    )
    if updated != 1:
        db.rollback()
        return None
    db.flush()
    db.expire_all()
    db.commit()
    return clean


def claim_note_auto_title(
    db: Session,
    *,
    thread_id: str,
    expected_title: str,
    proposed_title: str,
) -> str | None:
    """Apply a generated note title only while its default title is unchanged."""
    clean = clean_generated_title(proposed_title)[:255]
    if not clean or clean == expected_title:
        return None
    updated = (
        db.query(NoteThread)
        .filter(
            NoteThread.id == str(thread_id),
            NoteThread.title_source == "default",
            NoteThread.title == expected_title,
        )
        .update(
            {
                NoteThread.title: clean,
                NoteThread.title_source: "auto",
                NoteThread.updated_at: datetime.now(timezone.utc),
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        db.rollback()
        return None
    db.commit()
    return clean
