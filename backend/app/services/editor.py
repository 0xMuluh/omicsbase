"""Editor service — allows users to modify generated R/Quarto projects via natural language OmicsBase prompts."""

from __future__ import annotations

import json
import logging
from dataclasses import replace as dataclass_replace
from pathlib import Path
from typing import Any

from app.services.apply_edits import (
    ApplyResult,
    format_apply_failures,
)
from app.services.context_budget import bounded_json
from app.services.edit_engine import (
    EditEngineError,
    EditOperation,
    EditPolicy,
    commit_transaction,
    parse_apply_patch,
    prepare_transaction,
    sha256_bytes,
)
from app.services.llm import call_llm
from app.services.report_pack import ReportPackError, load_report_pack

logger = logging.getLogger(__name__)

TEXT_EXTENSIONS = {".R", ".r", ".qmd", ".yml", ".yaml", ".md"}
MAX_FILE_CHARS = 12000
MAX_CONTEXT_CHARS = 60000
MAX_EDIT_REFLECTIONS = 2


async def edit_generated_project(
    project_dir: str,
    instruction: str,
    *,
    project_context: dict[str, Any] | None = None,
    analysis_plan: dict[str, Any] | None = None,
    study_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Modify generated source using the approved study and pack contract."""
    base = Path(project_dir).resolve()
    contract_context, protected_paths = _editor_contract_context(base)
    source_files = _collect_source_files(
        base,
        file_roles=contract_context.get("file_roles") or {},
        protected_paths=protected_paths,
    )
    prompt_context = dict(project_context or {})
    if analysis_plan is not None:
        prompt_context["analysis_plan"] = analysis_plan
    if study_manifest is not None:
        prompt_context["study_manifest"] = study_manifest
    prompt_context["protected_paths"] = sorted(protected_paths)
    if not source_files:
        return {"status": "failed", "reason": "No generated source files found in project to edit."}

    system_prompt = """You are an expert scientific R and Quarto developer assistant.
The user wants to modify an existing omics analysis project.
You will receive the user's edit instruction and the current source files.
Your job is to modify the files that need to be changed to fulfill the instruction.

You can specify edits in one of three ways:
1. Targeted Search-and-Replace (PREFERRED for small edits): provide "search" and "replace".
2. Codex-style patch envelope: provide a "patch" with explicit file paths and hunks.
3. Full File Replacement (only for new files or restructuring >50%): provide "content".

Rules:
1. Return ONLY valid JSON matching the required schema.
2. Keep edits minimal, clean, and publication-quality.
3. Make sure all R syntax is valid and ggplot2 figures use readable themes.
4. Preserve existing data loading logic in data.R unless explicitly asked to change it.
5. Only include files in "edits" that actually need modifications.
6. SEARCH blocks must match one current location; do not guess or use a near match.
7. Do not include shell commands, absolute paths, or edits outside the supplied source files.
8. A file marked truncated may only receive a targeted SEARCH/REPLACE or patch hunk; never reconstruct it from the excerpt.
9. The server prepares every edit before committing the batch; a failed edit aborts the whole batch.
10. Treat the approved plan and study manifest as authoritative context.
11. Never edit protected validator or contract paths; the server will reject such operations.
"""

    user_prompt = _build_edit_prompt(
        source_files,
        instruction,
        project_context=prompt_context,
        protected_paths=protected_paths,
    )
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
                "reason": result.get("summary") or "OmicsBase editor returned no file modifications.",
                "apply_results": [item.to_dict() for item in apply_results],
            }

        summary = result.get("summary", summary)
        apply_results = _apply_edits(base, edits, protected_paths=protected_paths)
        failures = [item for item in apply_results if not item.ok]
        if not failures:
            break
        if attempt >= MAX_EDIT_REFLECTIONS:
            break
        user_prompt = (
            _build_edit_prompt(
                source_files,
                instruction,
                project_context=prompt_context,
                protected_paths=protected_paths,
            )
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


def _apply_edits(
    base: Path,
    edits: list[Any],
    *,
    protected_paths: set[str] | None = None,
) -> list[ApplyResult]:
    """Prepare and commit all model edits as one transaction.

    A malformed operation is a batch failure, even when sibling operations are
    valid. This keeps reflection prompts truthful and prevents partial writes.
    Patch envelopes may carry their own file paths, so the outer ``path`` is
    optional for that representation.
    """
    operations: list[EditOperation] = []
    metadata: list[tuple[str, str | None]] = []
    results: list[ApplyResult] = []
    preflight_failed = False

    def fail(path: str, message: str, *, attempted_search: str | None = None) -> None:
        nonlocal preflight_failed
        preflight_failed = True
        results.append(
            ApplyResult(
                path=path,
                ok=False,
                strategy="none",
                attempted_search=attempted_search,
                diagnostics=[message],
            )
        )

    for edit in edits:
        if not isinstance(edit, dict):
            fail("", "Edit operation must be an object.")
            continue
        relative_path = str(edit.get("path") or "").strip()
        search_str = edit.get("search")
        replace_str = edit.get("replace")
        full_content = edit.get("content")
        patch_text = edit.get("patch")
        reason = str(edit.get("reason") or "model editor request")[:1000]

        if isinstance(patch_text, str):
            try:
                patch_operations = parse_apply_patch(patch_text)
            except EditEngineError as exc:
                fail(relative_path or "(patch)", str(exc))
                continue
            for patch_operation in patch_operations:
                rel = str(patch_operation.path or "").strip()
                target = _safe_source_path(base, rel)
                if target is None:
                    fail(rel, "Unsafe or unsupported edit path.")
                    continue
                if target.exists() and not target.is_file():
                    fail(rel, "Edit target is not a regular file.")
                    continue
                existing = target.read_bytes() if target.exists() else None
                base_sha256 = sha256_bytes(existing)
                operations.append(
                    dataclass_replace(
                        patch_operation,
                        base_sha256=base_sha256,
                        reason=reason,
                    )
                )
                metadata.append((rel, None))
            continue

        if not relative_path:
            fail("", "Edit path is required unless a patch envelope supplies paths.")
            continue
        target = _safe_source_path(base, relative_path)
        if target is None:
            fail(relative_path, "Unsafe or unsupported edit path.")
            continue
        rel = target.relative_to(base).as_posix()
        if target.exists() and not target.is_file():
            fail(rel, "Edit target is not a regular file.")
            continue
        existing = target.read_bytes() if target.exists() else None
        base_sha256 = sha256_bytes(existing)

        if isinstance(search_str, str) and isinstance(replace_str, str):
            if existing is None:
                fail(rel, "SEARCH/REPLACE target does not exist.", attempted_search=search_str)
                continue
            operations.append(
                EditOperation(
                    path=rel,
                    kind="replace",
                    search=search_str,
                    replace=replace_str,
                    allow_multiple=bool(edit.get("allow_multiple", False)),
                    base_sha256=base_sha256,
                    reason=reason,
                )
            )
            metadata.append((rel, search_str))
        elif isinstance(full_content, str):
            if existing is not None and len(existing) > MAX_FILE_CHARS:
                fail(
                    rel,
                    "Full-file rewrites are disabled when the model saw a truncated source; use an exact SEARCH/REPLACE or patch hunk.",
                )
                continue
            operations.append(
                EditOperation(
                    path=rel,
                    kind="rewrite" if existing is not None else "create",
                    content=full_content,
                    base_sha256=base_sha256,
                    reason=reason,
                )
            )
            metadata.append((rel, None))
        else:
            fail(rel, "Edit missing search/replace, patch, and content.")

    if preflight_failed:
        for relative_path, attempted_search in metadata:
            results.append(
                ApplyResult(
                    path=relative_path,
                    ok=False,
                    attempted_search=attempted_search,
                    diagnostics=["Edit transaction aborted during preflight; no files were changed."],
                    reason="preflight_failed",
                )
            )
        return results
    if not operations:
        return results

    try:
        prepared = prepare_transaction(
            base,
            operations,
            origin="editor",
            summary="Natural-language project edit",
            policy=EditPolicy(protected_paths=frozenset(protected_paths or set())),
            validate=True,
        )
        committed = commit_transaction(prepared)
    except EditEngineError as exc:
        for relative_path, attempted_search in metadata:
            results.append(
                ApplyResult(
                    path=relative_path,
                    ok=False,
                    strategy="conflict" if exc.code == "edit_conflict" else "none",
                    attempted_search=attempted_search,
                    diagnostics=[str(exc)],
                    reason=exc.code,
                )
            )
        return results

    prepared_by_path = {item.path: item for item in committed.files}
    for relative_path, attempted_search in metadata:
        item = prepared_by_path.get(relative_path)
        if item is None:
            continue
        results.append(
            ApplyResult(
                path=relative_path,
                ok=True,
                strategy=item.strategies[-1] if item.strategies else "none",
                before=_decode_text(item.before),
                after=_decode_text(item.after),
                attempted_search=attempted_search,
                reason="Committed transaction",
            )
        )
    return results

def _decode_text(value: bytes | None) -> str | None:
    return value.decode("utf-8", errors="replace") if value is not None else None

def _editor_contract_context(base: Path) -> tuple[dict[str, Any], set[str]]:
    """Load materialized ReportPack roles and paths the editor must protect."""
    protected = {
        "execution_contract.json",
        "report_pack.yaml",
        ".omicsbase/capabilities.json",
    }
    metadata: dict[str, Any] = {}
    file_roles: dict[str, dict[str, Any]] = {}
    try:
        pack = load_report_pack(base, manifest_name="report_pack.yaml")
        metadata = pack.metadata()
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in TEXT_EXTENSIONS:
                continue
            relative = path.relative_to(base).as_posix()
            if any(part.startswith(".") for part in path.relative_to(base).parts):
                continue
            try:
                file_roles[relative] = pack.classify(relative).as_dict()
            except ReportPackError:
                continue
        if pack.execution:
            protected.update(
                step.path
                for step in pack.execution.steps
                if step.role == "validator"
            )
        for capability in pack.capabilities:
            protected.update(capability.validators)
    except (OSError, UnicodeDecodeError, ReportPackError, ValueError) as exc:
        metadata = {
            "status": "unavailable",
            "error": str(exc)[:500],
            "source": "materialized_report_pack",
        }
    return {"metadata": metadata, "file_roles": file_roles}, protected


def _collect_source_files(
    base: Path,
    *,
    file_roles: dict[str, dict[str, Any]] | None = None,
    protected_paths: set[str] | None = None,
) -> list[dict[str, Any]]:
    files = []
    protected = protected_paths or set()
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.suffix not in TEXT_EXTENSIONS:
            continue
        relative = path.relative_to(base).as_posix()
        if any(part.startswith(".") for part in path.relative_to(base).parts):
            continue
        raw = path.read_bytes()
        content = raw.decode("utf-8", errors="replace")
        files.append({
            "path": relative,
            "content": content[:MAX_FILE_CHARS],
            "complete": len(content) <= MAX_FILE_CHARS,
            "sha256": sha256_bytes(raw) or "",
            "protected": relative in protected,
        })
    return files


def _build_edit_prompt(
    source_files: list[dict[str, Any]],
    instruction: str,
    *,
    project_context: dict[str, Any] | None = None,
    protected_paths: set[str] | None = None,
) -> str:
    context_parts = []
    total_chars = 0
    for source_file in source_files:
        digest = source_file.get("sha256", "unknown")
        completeness = "complete" if source_file.get("complete", False) else "truncated; targeted edits only"
        protection = "PROTECTED" if source_file.get("protected") else "editable"
        block = (
            f"### {source_file['path']} ({protection}; sha256: {digest}; {completeness})\n```\n"
            f"{source_file['content']}\n```"
        )
        if total_chars + len(block) > MAX_CONTEXT_CHARS:
            break
        context_parts.append(block)
        total_chars += len(block)

    source_context = "\n\n".join(context_parts)
    context = project_context or {}
    protected = sorted(protected_paths or set())
    return f"""User Request:
"{instruction}"

## Approved project context

```json
{bounded_json(context, 14000, priority_keys=("project", "analysis_plan", "study_manifest", "protected_paths"))}
```

## Protected paths

{json.dumps(protected, ensure_ascii=False)}

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
      "replace": "new updated code block",
      "reason": "why this change is required"
    }},
    {{
      "patch": "*** Begin Patch\n*** Update File: code/alpha/alpha.qmd\n...\n*** End Patch",
      "reason": "why this change is required"
    }},
    {{
      "path": "code/new_file.R",
      "content": "complete new file content",
      "reason": "why this file is needed"
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
