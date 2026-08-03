from types import SimpleNamespace


from app.services.workspace_agent import (
    _conversation_messages,
    _workspace_live_context,
)


def test_replayable_history_excludes_unpaired_tool_events():
    messages = _conversation_messages(
        [
            SimpleNamespace(role="user", content="Inspect the project"),
            SimpleNamespace(role="assistant", content="I found the report."),
            SimpleNamespace(role="tool", content="list_files completed"),
        ]
    )

    assert messages == [
        {"role": "user", "content": "Inspect the project"},
        {"role": "assistant", "content": "I found the report."},
    ]


def test_live_context_is_separate_from_stable_prompt():
    context = _workspace_live_context({"status": "completed"})

    assert context.startswith("## Current workspace snapshot")
    assert "\"status\": \"completed\"" in context
