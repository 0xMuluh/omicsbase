from __future__ import annotations

import json
from app.services.opencode_client import (
    _is_missing_session_error,
    _looks_like_opencode_session,
    assistant_error_from_info,
    build_opencode_env,
    clear_opencode_session,
    collect_assistant_parts,
    load_opencode_session,
    opencode_runtime_config,
    resolve_model_spec,
    save_opencode_session,
)


def test_project_uuid_is_not_an_opencode_session():
    assert not _looks_like_opencode_session("7d635618-81f5-40f9-ad9b-4f6c466c3b5b")
    assert _looks_like_opencode_session("ses_01K3ABC")
    assert _is_missing_session_error("Error: Session not found")


def test_opencode_session_roundtrip(tmp_path):
    assert load_opencode_session(tmp_path) is None
    save_opencode_session(tmp_path, "ses_01K3ABC")
    assert load_opencode_session(tmp_path) == "ses_01K3ABC"
    clear_opencode_session(tmp_path)
    assert load_opencode_session(tmp_path) is None


def test_resolve_model_spec_maps_gemini_to_google():
    assert resolve_model_spec("gemini", "gemini-3.6-flash") == "google/gemini-3.6-flash"


def test_runtime_config_pins_google_model_and_disables_orcarouter():
    config = json.loads(
        opencode_runtime_config("/tmp/some-project", model_spec="google/gemini-3.6-flash", provider="gemini")
    )
    assert config["model"] == "google/gemini-3.6-flash"
    assert "orcarouter" in config["disabled_providers"]
    assert "openrouter" in config["disabled_providers"]
    assert "gemini-3.6-flash" in config["provider"]["google"]["models"]
    assert config["mcp"]["omicsbase"]["environment"]["OMICSBASE_PROJECT_DIR"] == "/tmp/some-project"


def test_collect_assistant_parts_splits_reasoning_and_response():
    response, reasoning = collect_assistant_parts(
        [
            {"type": "reasoning", "text": "Inspect data/ first."},
            {"type": "text", "text": "Yes, I am here."},
        ]
    )
    assert response == "Yes, I am here."
    assert reasoning == "Inspect data/ first."


def test_assistant_error_from_info_extracts_provider_auth_error():
    message = assistant_error_from_info(
        {
            "role": "assistant",
            "error": {
                "name": "ProviderAuthError",
                "data": {
                    "providerID": "google",
                    "message": "Google Generative AI API key is missing.",
                },
            },
        }
    )
    assert message == "ProviderAuthError: Google Generative AI API key is missing."


def test_gemini_env_does_not_leak_orcarouter_keys(monkeypatch):
    from app.services import opencode_client

    monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-orca-test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-orca-test")
    monkeypatch.setenv("GEMINI_API_KEY", "from-process-env")
    monkeypatch.setattr(opencode_client.settings, "gemini_api_key", "gemini-test-key")
    monkeypatch.setattr(opencode_client.settings, "orcarouter_api_key", "sk-orca-test")
    monkeypatch.setattr(opencode_client.settings, "openrouter_api_key", "sk-orca-test")

    env = build_opencode_env(provider="gemini")
    assert env["GOOGLE_GENERATIVE_AI_API_KEY"] == "gemini-test-key"
    assert "GEMINI_API_KEY" not in env
    assert "ORCAROUTER_API_KEY" not in env
    assert "OPENROUTER_API_KEY" not in env


def test_iter_stdout_lines_handles_oversized_json_event():
    import asyncio

    from app.services.opencode_client import _iter_stdout_lines

    payload = json.dumps({"type": "tool_use", "output": "x" * 200_000}) + "\n"

    async def run():
        reader = asyncio.StreamReader(limit=64 * 1024)
        reader.feed_data(payload.encode())
        reader.feed_eof()
        lines = [line async for line in _iter_stdout_lines(reader)]
        assert len(lines) == 1
        assert json.loads(lines[0])["type"] == "tool_use"

    asyncio.run(run())
