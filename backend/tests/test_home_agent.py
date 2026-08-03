"""Home chat decides reply vs start_study without creating a project first."""

from __future__ import annotations

import pytest

from app.services import home_agent


@pytest.mark.asyncio
async def test_home_chat_replies_to_capabilities(monkeypatch):
    async def fake_llm(**kwargs):
        return '{"type":"reply","message":"I build Quarto omics reports from your data."}'

    monkeypatch.setattr(home_agent, "call_llm", fake_llm)
    events = [event async for event in home_agent.stream_home_chat("what can you do for me?")]
    assert events[-1]["type"] == "final"
    assert "Quarto" in events[-1]["message"]
    assert not any(event["type"] == "start_study" for event in events)


@pytest.mark.asyncio
async def test_home_chat_can_start_study(monkeypatch):
    async def fake_llm(**kwargs):
        return (
            '{"type":"start_study","name":"GlobalPatterns demo",'
            '"question":"Use GlobalPatterns for a standard microbiome report",'
            '"message":"I will set up a workspace with GlobalPatterns.",'
            '"use_example":"phyloseq::GlobalPatterns"}'
        )

    monkeypatch.setattr(home_agent, "call_llm", fake_llm)
    events = [
        event
        async for event in home_agent.stream_home_chat(
            "Use GlobalPatterns and build a standard microbiome report"
        )
    ]
    assert events[-1]["type"] == "start_study"
    assert events[-1]["use_example"] == "phyloseq::GlobalPatterns"
