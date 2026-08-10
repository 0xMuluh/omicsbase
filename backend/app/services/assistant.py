"""LLM-powered workspace assistant for project Q&A."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.llm import call_llm

logger = logging.getLogger(__name__)

EDIT_VERBS = (
    "add",
    "change",
    "edit",
    "fix",
    "remove",
    "replace",
    "rerun",
    "render",
    "update",
    "modify",
    "make",
    "show",
    "plot",
    "include",
    "exclude",
)

MAX_SOURCE_CHARS = 3500
MAX_REPORT_CHARS = 5000
MAX_HISTORY_MESSAGES = 12

def is_edit_prompt(instruction: str) -> bool:
    normalized = " ".join(instruction.lower().strip().split())
    if not normalized:
        return False
    return any(normalized.startswith(f"{verb} ") or normalized == verb for verb in EDIT_VERBS)

def _load_assistant_prompt() -> str:
    path = Path(settings.prompts_dir) / "assistant.md"
    if path.exists():
        return path.read_text()
    return "You are an omics analysis assistant. Answer questions using only the provided project context."

def _strip_html(html: str, max_chars: int = MAX_REPORT_CHARS) -> str:
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]

def _read_excerpt(path: Path, max_chars: int = MAX_SOURCE_CHARS) -> str | None:
    if not path.is_file():
        return None
    content = path.read_text(errors="replace")
    if len(content) <= max_chars:
        return content
    return content[:max_chars] + f"\n\n... [truncated, {len(content)} chars total]"

def _context_source_paths(project: Any, base: Path) -> list[str]:
    """Select excerpts from the active ReportPack inventory, not fixed filenames."""
    plan = getattr(project, "analysis_plan", None) or {}
    domain = str(plan.get("domain") or "microbiome").strip().lower()
    pack = None
    try:
        from app.services.spawner import resolve_report_pack

        pack = resolve_report_pack(
            str(plan.get("report_pack_id") or "").strip() or None,
            domain=domain,
        )
    except Exception:
        # The legacy endpoint must remain useful for projects created before
        # ReportPack metadata existed; dynamic file discovery is the fallback.
        pack = None

    candidates: list[tuple[int, str]] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(base).as_posix()
        parts = Path(relative).parts
        if any(part.startswith(".") for part in parts) or relative.startswith(("data/", "output/")):
            continue
        if path.suffix.lower() not in {".r", ".qmd", ".rmd", ".md", ".yaml", ".yml", ".txt"}:
            continue
        role = ""
        if pack is not None:
            try:
                role = pack.classify(relative).role
            except Exception:
                role = ""
        role_rank = {
            "orchestrator": 0,
            "data_loader": 1,
            "helper": 2,
            "assembly": 3,
            "page": 4,
        }.get(role, 5)
        suffix_rank = 0 if path.suffix.lower() in {".r", ".qmd", ".rmd"} else 1
        candidates.append((role_rank * 2 + suffix_rank, relative))
    return [relative for _, relative in sorted(candidates)[:12]]

def _latest_review(project) -> dict[str, Any] | None:
    for action in reversed(project.agent_actions or []):
        if action.get("type") == "review":
            return {
                "status": action.get("status"),
                "summary": action.get("summary"),
                "checks": (action.get("details") or {}).get("checks") or [],
            }
    return None

def _summarize_uploaded_files(project) -> list[dict[str, Any]]:
    summaries = []
    for file_record in project.files or []:
        file_summary = file_record.file_summary or {}
        summaries.append(
            {
                "name": file_record.original_name,
                "role": file_record.file_role,
                "format": file_record.detected_format,
                "dimensions": file_summary.get("dimensions"),
                "columns": (file_summary.get("columns") or [])[:40],
            }
        )
    return summaries

def _summarize_plan(plan: dict[str, Any]) -> dict[str, Any]:
    workflow = plan.get("workflow") or []
    steps = []
    for step in workflow:
        if not step.get("enabled"):
            continue
        entry: dict[str, Any] = {
            "id": step.get("id"),
            "name": step.get("name"),
            "classification": step.get("classification"),
            "rationale": step.get("rationale"),
        }
        if step.get("ensemble_methods"):
            entry["ensemble_methods"] = [
                method.get("name") or method.get("id")
                for method in step["ensemble_methods"]
            ]
        steps.append(entry)
    return {
        "project_name": plan.get("project_name"),
        "study_type": plan.get("study_type"),
        "question": plan.get("question"),
        "grouping_variable": plan.get("grouping_variable"),
        "group_levels": plan.get("group_levels") or [],
        "workflow": steps,
        "notes": plan.get("notes"),
    }

def build_project_context(project) -> str:
    """Assemble compact project context for the assistant LLM."""
    context: dict[str, Any] = {
        "project": {
            "name": project.name,
            "question": project.question,
            "notes": project.notes,
            "status": project.status,
            "agent_state": project.agent_state,
        },
        "uploaded_files": _summarize_uploaded_files(project),
        "agent_memory": project.agent_memory or {},
        "recent_agent_actions": (project.agent_actions or [])[-10:],
        "quality_review": _latest_review(project),
    }

    if project.analysis_plan:
        context["analysis_plan"] = _summarize_plan(project.analysis_plan)

    if project.project_dir:
        base = Path(project.project_dir)
        generated_files = []
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(base).as_posix()
            if any(part.startswith(".") for part in Path(relative).parts):
                continue
            generated_files.append(relative)
            if len(generated_files) >= 80:
                break
        context["generated_files"] = generated_files

        excerpts: dict[str, str] = {}
        for relative_path in _context_source_paths(project, base):
            excerpt = _read_excerpt(base / relative_path)
            if excerpt:
                excerpts[relative_path] = excerpt
        if excerpts:
            context["source_excerpts"] = excerpts

        report_html = base / "output" / "index.html"
        if report_html.exists():
            context["rendered_report_excerpt"] = _strip_html(report_html.read_text(errors="replace"))

    return json.dumps(context, indent=2, default=str)

def _format_history(history: list[dict[str, str]] | None) -> str:
    if not history:
        return "(No prior conversation in this session.)"

    lines = []
    for message in history[-MAX_HISTORY_MESSAGES:]:
        role = message.get("role", "user").capitalize()
        content = (message.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines) if lines else "(No prior conversation in this session.)"

async def respond_to_prompt(
    project,
    instruction: str,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Return an assistant response, using the LLM for open Q&A."""
    normalized = " ".join(instruction.lower().strip().split())
    if not normalized:
        return {
            "type": "guidance",
            "message": "Ask about methods, results, or workflow — or request a concrete edit like “Add a PERMANOVA section”.",
        }

    if is_edit_prompt(instruction):
        return {
            "type": "edit_suggestion",
            "message": "That sounds like an edit request. I'll apply it to the generated source and re-render the report.",
            "instruction": instruction.strip(),
        }

    system_prompt = _load_assistant_prompt()
    project_context = build_project_context(project)
    conversation = _format_history(history)

    user_prompt = f"""## Project context

```json
{project_context}
```

## Conversation so far

{conversation}

## Current user message

{instruction.strip()}

Answer the user's message using only the project context above. If they are asking about results or statistics that are not present in the context, say what is missing instead of guessing."""

    answer = await call_llm(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_format="text",
        max_tokens=2500,
    )
    message = answer.strip()
    return {"type": "answer", "message": message}
