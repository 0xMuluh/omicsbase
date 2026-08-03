"""Repair service for generated R/Quarto projects after render failures."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.services.apply_edits import (
    ApplyResult,
    apply_rewrite,
    apply_search_replace,
    format_apply_failures,
    is_path_locked,
)
from app.services.llm import call_llm
from app.services.sanitizer import sanitize_text

logger = logging.getLogger(__name__)

TEXT_EXTENSIONS = {".R", ".r", ".qmd", ".yml", ".yaml", ".md"}
DEFAULT_FILE_CHARS = 12000
REFERENCED_FILE_CHARS = 50000
MAX_CONTEXT_CHARS = 100000
MAX_REPAIR_REFLECTIONS = 2


async def repair_generated_project(
    project_dir: str,
    failure_result: dict[str, Any],
    repair_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Ask the LLM for targeted file repairs and apply them safely."""
    base = Path(project_dir).resolve()
    failure_text = json.dumps(failure_result, default=str)
    source_files = _collect_source_files(base, failure_text)
    if not source_files:
        return {"status": "skipped", "reason": "No generated source files were available to repair."}

    system_prompt = """You are a precise R and Quarto repair agent for generated microbiome analysis projects.
You receive render/runtime errors, repair history, and generated source files. Return only valid JSON.
You may only repair files that are present in the provided source context.
Do not invent input files, metadata columns, comparison groups, or biological conclusions.
Prefer minimal, robust patches that make the project render honestly.
If a required R package is optional, avoid it or add a guarded fallback instead of assuming installation.

You may specify repairs as either:
1. Targeted Search-and-Replace: "search" (code string) and "replace" (new code string). Whitespace-tolerant matching is applied.
2. Full File Replacement: "content" (complete new file text).
"""
    user_prompt = _build_repair_prompt(source_files, failure_result, repair_history)
    apply_results: list[ApplyResult] = []
    reason = "Targeted render repair"

    for attempt in range(MAX_REPAIR_REFLECTIONS + 1):
        response = await call_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_format="json",
            max_tokens=12000,
        )
        repair_plan = _parse_repair_response(response)
        repairs = repair_plan.get("repairs", [])
        if not isinstance(repairs, list) or not repairs:
            return {
                "status": "skipped",
                "reason": repair_plan.get("reason") or "Repair agent returned no file edits.",
                "raw_response": response[:2000],
                "apply_results": [item.to_dict() for item in apply_results],
            }

        # Check for duplicate repair pass
        if repair_history:
            for past_pass in repair_history:
                past_repairs = past_pass.get("repair", {}).get("repairs", [])
                if repairs == past_repairs:
                    return {
                        "status": "skipped",
                        "reason": "Repair agent returned an identical repair pass to a previous failed edit.",
                        "raw_response": response[:2000],
                        "apply_results": [item.to_dict() for item in apply_results],
                    }

        reason = str(repair_plan.get("reason") or reason)
        apply_results = _apply_repairs(base, repairs)
        failures = [item for item in apply_results if not item.ok]
        if not failures:
            break
        if attempt >= MAX_REPAIR_REFLECTIONS:
            break
        user_prompt = (
            _build_repair_prompt(source_files, failure_result, repair_history)
            + "\n\n## Previous apply failures — fix only these SEARCH/REPLACE blocks\n"
            + format_apply_failures(failures)
        )

    applied = [item for item in apply_results if item.ok]
    if not applied:
        return {
            "status": "skipped",
            "reason": format_apply_failures(apply_results) or "No safe repair edits were returned.",
            "apply_results": [item.to_dict() for item in apply_results],
        }

    return {
        "status": "repaired",
        "repairs": [{"path": item.path, "reason": item.reason or reason, "strategy": item.strategy} for item in applied],
        "apply_results": [item.to_dict() for item in apply_results],
    }


def _apply_repairs(base: Path, repairs: list[Any]) -> list[ApplyResult]:
    results: list[ApplyResult] = []
    for repair in repairs:
        if not isinstance(repair, dict):
            continue
        relative_path = str(repair.get("path") or "").strip()
        reason = str(repair.get("reason") or "Targeted render repair")
        if not relative_path:
            continue
        target = _safe_source_path(base, relative_path)
        if target is None:
            results.append(
                ApplyResult(
                    path=relative_path,
                    ok=False,
                    strategy="none",
                    diagnostics=["Unsafe or missing repair target."],
                    reason=reason,
                )
            )
            continue
        rel = target.relative_to(base).as_posix()
        if is_path_locked(base, rel):
            results.append(
                ApplyResult(
                    path=rel,
                    ok=False,
                    strategy="locked",
                    diagnostics=[f"{rel} is locked and cannot be repaired by the agent."],
                    reason=reason,
                )
            )
            continue

        search_str = repair.get("search")
        replace_str = repair.get("replace")
        content = repair.get("content")
        existing = target.read_text(errors="replace")

        if isinstance(search_str, str) and isinstance(replace_str, str):
            result = apply_search_replace(existing, search_str, replace_str, path=rel)
            result.reason = reason
            if result.ok and result.after is not None:
                target.write_text(result.after)
                results.append(result)
            elif isinstance(content, str):
                rewrite = apply_rewrite(existing, content, path=rel)
                rewrite.reason = reason
                target.write_text(content)
                results.append(rewrite)
            else:
                results.append(result)
        elif isinstance(content, str):
            rewrite = apply_rewrite(existing, content, path=rel)
            rewrite.reason = reason
            target.write_text(content)
            results.append(rewrite)
        else:
            results.append(
                ApplyResult(
                    path=rel,
                    ok=False,
                    strategy="none",
                    diagnostics=["Repair missing both search/replace and content."],
                    reason=reason,
                )
            )
    return results


def _collect_source_files(base: Path, failure_text: str) -> list[dict[str, str]]:
    files = []
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.suffix not in TEXT_EXTENSIONS:
            continue
        relative = path.relative_to(base).as_posix()
        if any(part.startswith(".") for part in path.relative_to(base).parts):
            continue
        content = path.read_text(errors="replace")
        is_referenced = relative in failure_text or Path(relative).name in failure_text
        limit = REFERENCED_FILE_CHARS if is_referenced else DEFAULT_FILE_CHARS
        files.append({"path": relative, "content": content[:limit], "referenced": str(is_referenced).lower()})

    return sorted(files, key=lambda source_file: (source_file["referenced"] != "true", source_file["path"]))


def _build_repair_prompt(
    source_files: list[dict[str, str]],
    failure_result: dict[str, Any],
    repair_history: list[dict[str, Any]] | None = None,
) -> str:
    context_parts = []
    total_chars = 0
    for source_file in source_files:
        block = f"### {source_file['path']}\n```\n{source_file['content']}\n```"
        if total_chars + len(block) > MAX_CONTEXT_CHARS:
            break
        context_parts.append(block)
        total_chars += len(block)

    failure_json = sanitize_text(json.dumps(failure_result, indent=2, default=str)[-20000:])
    history_context = ""
    if repair_history:
        history_json = sanitize_text(json.dumps(repair_history, indent=2, default=str)[-8000:])
        history_context = f"\n\n## Previous Failed Repair Attempts\nDo NOT repeat these failed edits:\n```json\n{history_json}\n```\n"

    source_context = "\n\n".join(context_parts)
    return f"""Repair this generated R/Quarto project after a failed render.

## Failure Result

```json
{failure_json}
```
{history_context}
## Generated Source Files

{source_context}

## Required JSON Output

Return exactly this shape:

{{
  "reason": "short diagnosis",
  "repairs": [
    {{
      "path": "code/data.R",
      "reason": "why this file needs changing",
      "search": "exact snippet to replace (or use content)",
      "replace": "new snippet"
    }}
  ]
}}

Rules:
- Only include files shown in Generated Source Files.
- If the traceback names a .qmd or .R file, repair that file first.
- Keep repairs minimal and render-oriented.
- If a namespace export does not exist, use the correct package or replace it with base/tidyverse code already loaded.
- If the error is a missing nonessential package, remove or guard that dependency.
- If the error is a missing column/group, infer only from shown code and fail honestly if not knowable.
"""


def _parse_repair_response(response: str) -> dict[str, Any]:
    text = response.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline >= 0:
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Repair response was not valid JSON: %s", response[:500])
        return {"reason": "Repair agent returned invalid JSON.", "repairs": []}
    return parsed if isinstance(parsed, dict) else {"reason": "Repair agent returned a non-object JSON value.", "repairs": []}


def _safe_source_path(base: Path, relative_path: str) -> Path | None:
    path = (base / relative_path).resolve()
    try:
        path.relative_to(base)
    except ValueError:
        return None
    if path.suffix not in TEXT_EXTENSIONS:
        return None
    if not path.exists() or not path.is_file():
        return None
    return path
