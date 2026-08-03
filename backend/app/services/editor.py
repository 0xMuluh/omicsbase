"""Editor service — allows users to modify generated R/Quarto projects via natural language AI prompts."""

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

logger = logging.getLogger(__name__)

TEXT_EXTENSIONS = {".R", ".r", ".qmd", ".yml", ".yaml", ".md"}
MAX_FILE_CHARS = 12000
MAX_CONTEXT_CHARS = 60000
MAX_EDIT_REFLECTIONS = 2


async def edit_generated_project(project_dir: str, instruction: str) -> dict[str, Any]:
    """Modify an existing generated project based on a user's natural language instruction."""
    base = Path(project_dir).resolve()
    source_files = _collect_source_files(base)
    if not source_files:
        return {"status": "failed", "reason": "No generated source files found in project to edit."}

    system_prompt = """You are an expert scientific R and Quarto developer assistant.
The user wants to modify an existing microbiome analysis project.
You will receive the user's edit instruction and the current source files.
Your job is to modify the files that need to be changed to fulfill the instruction.

You can specify edits in either of two ways:
1. Targeted Search-and-Replace (PREFERRED for small edits):
   Provide "search" (exact snippet from current file) and "replace" (new snippet to insert).
2. Full File Replacement (PREFERRED when creating new files or restructuring >50% of the file):
   Provide "content" with the full updated file.

Rules:
1. Return ONLY valid JSON matching the required schema.
2. Keep edits minimal, clean, and publication-quality.
3. Make sure all R syntax is valid and ggplot2 figures use readable themes.
4. Preserve existing data loading logic in data.R unless explicitly asked to change it.
5. Only include files in "edits" that actually need modifications.
6. SEARCH blocks must match existing file text closely (whitespace differences are tolerated).
"""

    user_prompt = _build_edit_prompt(source_files, instruction)
    apply_results: list[ApplyResult] = []
    summary = "Successfully updated project files."

    for attempt in range(MAX_EDIT_REFLECTIONS + 1):
        response = await call_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_format="json",
            max_tokens=12000,
        )
        result = _parse_edit_response(response)
        edits = result.get("edits", [])
        if not isinstance(edits, list) or not edits:
            return {
                "status": "skipped",
                "reason": result.get("summary") or "AI editor returned no file modifications.",
                "apply_results": [item.to_dict() for item in apply_results],
            }

        summary = result.get("summary", summary)
        apply_results = _apply_edits(base, edits)
        failures = [item for item in apply_results if not item.ok]
        if not failures:
            break
        if attempt >= MAX_EDIT_REFLECTIONS:
            break
        user_prompt = (
            _build_edit_prompt(source_files, instruction)
            + "\n\n## Previous apply failures — fix only these SEARCH/REPLACE blocks\n"
            + format_apply_failures(failures)
        )

    applied = [item for item in apply_results if item.ok]
    if not applied:
        return {
            "status": "failed",
            "reason": format_apply_failures(apply_results) or "No safe edits were applied.",
            "apply_results": [item.to_dict() for item in apply_results],
        }

    return {
        "status": "completed",
        "summary": summary,
        "modified_files": [item.path for item in applied],
        "apply_results": [item.to_dict() for item in apply_results],
    }


def _apply_edits(base: Path, edits: list[Any]) -> list[ApplyResult]:
    results: list[ApplyResult] = []
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        relative_path = str(edit.get("path") or "").strip()
        if not relative_path:
            continue
        target = _safe_source_path(base, relative_path)
        if target is None:
            results.append(
                ApplyResult(
                    path=relative_path,
                    ok=False,
                    strategy="none",
                    diagnostics=["Unsafe or unsupported edit path."],
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
                    diagnostics=[f"{rel} is locked and cannot be edited by the agent."],
                    reason="locked",
                )
            )
            continue

        search_str = edit.get("search")
        replace_str = edit.get("replace")
        full_content = edit.get("content")
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = target.read_text(errors="replace") if target.exists() else None

        if isinstance(search_str, str) and isinstance(replace_str, str) and existing is not None:
            result = apply_search_replace(existing, search_str, replace_str, path=rel)
            if result.ok and result.after is not None:
                target.write_text(result.after)
                results.append(result)
            elif isinstance(full_content, str):
                rewrite = apply_rewrite(existing, full_content, path=rel)
                target.write_text(full_content)
                results.append(rewrite)
            else:
                results.append(result)
        elif isinstance(full_content, str):
            rewrite = apply_rewrite(existing, full_content, path=rel)
            target.write_text(full_content)
            results.append(rewrite)
        else:
            results.append(
                ApplyResult(
                    path=rel,
                    ok=False,
                    strategy="none",
                    diagnostics=["Edit missing both search/replace and content."],
                )
            )
    return results


def _collect_source_files(base: Path) -> list[dict[str, str]]:
    files = []
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.suffix not in TEXT_EXTENSIONS:
            continue
        relative = path.relative_to(base).as_posix()
        if any(part.startswith(".") for part in path.relative_to(base).parts):
            continue
        content = path.read_text(errors="replace")
        files.append({"path": relative, "content": content[:MAX_FILE_CHARS]})
    return files


def _build_edit_prompt(source_files: list[dict[str, str]], instruction: str) -> str:
    context_parts = []
    total_chars = 0
    for source_file in source_files:
        block = f"### {source_file['path']}\n```\n{source_file['content']}\n```"
        if total_chars + len(block) > MAX_CONTEXT_CHARS:
            break
        context_parts.append(block)
        total_chars += len(block)

    source_context = "\n\n".join(context_parts)
    return f"""User Request:
"{instruction}"

## Current Project Files:

{source_context}

## Output Format:

Return JSON with this exact structure:
{{
  "summary": "Short explanation of what changes were made",
  "edits": [
    {{
      "path": "code/alpha/alpha.qmd",
      "search": "exact code block to replace",
      "replace": "new updated code block"
    }},
    {{
      "path": "code/new_file.R",
      "content": "complete new file content"
    }}
  ]
}}
"""


def _parse_edit_response(response: str) -> dict[str, Any]:
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
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        logger.warning("Edit response parsing failed: %s", response[:300])
        return {}


def _safe_source_path(base: Path, relative_path: str) -> Path | None:
    path = (base / relative_path).resolve()
    try:
        path.relative_to(base)
    except ValueError:
        return None
    if path.suffix not in TEXT_EXTENSIONS:
        return None
    return path
