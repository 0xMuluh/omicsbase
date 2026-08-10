from __future__ import annotations

from app.services import skills
from app.services.note_agent import NOTE_TOOLS, NoteAgentExecutor
from app.services.tool_specs import TOOL_REGISTRY, workspace_tools


def test_jit_skill_listing_and_bounded_explicit_reference_loading(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    demo = root / "demo"
    references = demo / "references"
    references.mkdir(parents=True)
    (demo / "SKILL.md").write_text("# Demo skill\nUse this only for the demo method.\n")
    (references / "method.md").write_text("# Method\nUse the pinned method.\n")
    (references / "other.md").write_text("# Other\nThis must not be loaded implicitly.\n")
    monkeypatch.setattr(skills.settings, "skills_dir", str(root))

    listed = skills.list_skills({"limit": 5})
    assert listed["status"] == "ok"
    assert listed["skills"][0]["id"] == "demo"
    assert listed["skills"][0]["available_references"] == [
        "references/method.md",
        "references/other.md",
    ]

    loaded = skills.load_skill("demo", ["references/method.md"], max_chars=2000)
    assert loaded["status"] == "ok"
    assert loaded["skill"] == "demo"
    assert "Use this only for the demo method." in loaded["content"]
    assert [item["path"] for item in loaded["references"]] == ["references/method.md"]
    assert "This must not be loaded implicitly" not in loaded["references"][0]["content"]
    assert len(loaded["content"]) + len(loaded["references"][0]["content"]) <= 2000

    assert skills.load_skill("../demo")["status"] == "error"
    assert skills.load_skill("demo", ["../outside.md"])["status"] == "error"
    assert skills.load_skill("demo", ["SKILL.md"])["status"] == "error"


def test_jit_tools_are_advertised_for_both_lenses():
    for lens in ("workspace", "note"):
        list_spec = TOOL_REGISTRY.require("list_skills", lens=lens)
        load_spec = TOOL_REGISTRY.require("load_skill", lens=lens)
        assert list_spec.risk == "read"
        assert load_spec.parameters["required"] == ["skill"]
        assert load_spec.parameters["properties"]["references"]["maxItems"] == 8

    assert {item["function"]["name"] for item in workspace_tools()} >= {
        "list_skills",
        "load_skill",
    }
    assert {item["function"]["name"] for item in NOTE_TOOLS} >= {
        "list_skills",
        "load_skill",
    }


def test_note_executor_can_use_jit_tools_without_an_action_handler(tmp_path, monkeypatch):
    skill_root = tmp_path / "skills" / "demo"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text("# Demo\nA bounded instruction.\n")
    monkeypatch.setattr(skills.settings, "skills_dir", str(tmp_path / "skills"))
    executor = NoteAgentExecutor(message="inspect", cells=[], context={})

    import asyncio

    result = asyncio.run(
        executor.execute_tool(
            "load_skill",
            {"skill": "demo"},
            step=1,
            tool_call_id="jit-1",
            persisted_arguments={},
            step_text="",
        )
    )
    assert result.observation["status"] == "ok"
