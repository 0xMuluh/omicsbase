import json
from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.note_agent import (
    MAX_NOTE_HISTORY_CHARS,
    _live_context,
    conversation_from_cells,
)


def _cell(position: int, content: str):
    revision = SimpleNamespace(
        content=content,
        cell_type="markdown",
        language=None,
    )
    return SimpleNamespace(
        position=position,
        created_at=datetime.now(timezone.utc),
        revisions=[revision],
    )


def test_note_history_has_a_total_character_budget():
    messages = conversation_from_cells([_cell(index, "x" * 12000) for index in range(20)])

    assert len(messages) <= 12
    assert sum(len(message["content"]) for message in messages) <= MAX_NOTE_HISTORY_CHARS
    assert "older notebook history omitted" in messages[0]["content"] or len(messages) < 12


def test_live_context_does_not_repeat_replayable_cells():
    rendered = _live_context(
        {
            "thread": {"id": "thread-1"},
            "cells": [{"content": "large cell payload"}],
            "workspace_objects": ["counts"],
        }
    )
    payload = json.loads(rendered.split("json\n", 1)[1].rsplit("\n", 1)[0])

    assert "cells" not in payload
    assert payload["workspace_objects"] == ["counts"]
