"""Repair service for generated R/Quarto projects after render failures."""

from __future__ import annotations

import json
import logging
from dataclasses import replace as dataclass_replace
from pathlib import Path
from typing import Any

from app.services.apply_edits import ApplyResult, format_apply_failures
from app.services.edit_engine import EditEngineError, EditOperation, EditPolicy, commit_transaction, parse_apply_patch, prepare_transaction, sha256_bytes
from app.services.llm import call_llm
from app.services.sanitizer import sanitize_text
from app.services.execution_contract import (
    CONTRACT_NAME,
    PACK_SNAPSHOT_NAME,
    ExecutionContractError,
    load_execution_contract,
)

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
    protected_paths = _automatic_repair_protected_paths(base)
    source_files = _collect_source_files(
        base,
        failure_text,
        protected_paths=protected_paths,
    )
    if not source_files:
        return {"status": "skipped", "reason": "No generated source files were available to repair."}

    system_prompt = """You are a precise R and Quarto repair agent for generated omics analysis projects.
You receive render/runtime errors, repair history, and generated source files. Return only valid JSON.
You may only repair files that are present in the provided source context.
Do not invent input files, metadata columns, comparison groups, or biological conclusions.
Prefer minimal, robust patches that make the project render honestly.
If a required R package is optional, avoid it or add a guarded fallback instead of assuming installation.
ReportPack execution contracts, pack manifests, and scientific validator scripts are protected evidence.
Never edit or weaken them. Treat validator failures as diagnostics and repair an upstream loader,
analysis, helper, or report page only when the supplied source proves that change is valid.

You may specify repairs as one of three forms:
1. Targeted Search-and-Replace: "search" and "replace" for one unambiguous location.
2. Codex-style patch envelope: "patch" with explicit update hunks.
3. Full File Replacement: "content" only when a large, justified rewrite is necessary.
All repairs are prepared as one transaction; if any requested repair fails, none are written.
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
        apply_results = _apply_repairs(
            base,
            repairs,
            protected_paths=protected_paths,
        )
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


def _apply_repairs(
    base: Path,
    repairs: list[Any],
    *,
    protected_paths: set[str] | None = None,
) -> list[ApplyResult]:
    """Prepare all automatic repairs before committing any source bytes."""
    protected = protected_paths or set()
    operations: list[EditOperation] = []
    metadata: list[tuple[str, str | None, str]] = []
    results: list[ApplyResult] = []
    preflight_failed = False

    def fail(path: str, message: str, *, reason: str, attempted_search: str | None = None, strategy: str = "none") -> None:
        nonlocal preflight_failed
        preflight_failed = True
        results.append(
            ApplyResult(
                path=path,
                ok=False,
                strategy=strategy,
                attempted_search=attempted_search,
                diagnostics=[message],
                reason=reason,
            )
        )

    for repair in repairs:
        if not isinstance(repair, dict):
            fail("", "Repair operation must be an object.", reason="invalid_operation")
            continue
        relative_path = str(repair.get("path") or "").strip()
        reason = str(repair.get("reason") or "Targeted render repair")[:1000]
        patch = repair.get("patch")
        if isinstance(patch, str):
            try:
                patch_operations = parse_apply_patch(patch)
            except EditEngineError as exc:
                fail(relative_path or "(patch)", str(exc), reason=reason)
                continue
            for patch_operation in patch_operations:
                rel = str(patch_operation.path or "").strip()
                target = _safe_source_path(base, rel)
                if target is None or not target.is_file():
                    fail(rel, "Unsafe or missing repair target.", reason=reason)
                    continue
                if rel in protected:
                    fail(rel, f"{rel} is scientific assurance evidence and cannot be changed by automatic repair.", reason=reason, strategy="protected")
                    continue
                base_sha256 = sha256_bytes(target.read_bytes())
                operations.append(dataclass_replace(patch_operation, base_sha256=base_sha256, reason=reason))
                metadata.append((rel, None, reason))
            continue

        if not relative_path:
            fail("", "Repair path is required unless a patch envelope supplies paths.", reason=reason)
            continue
        target = _safe_source_path(base, relative_path)
        if target is None or not target.is_file():
            fail(relative_path, "Unsafe or missing repair target.", reason=reason)
            continue
        rel = target.relative_to(base).as_posix()
        if rel in protected:
            fail(rel, f"{rel} is scientific assurance evidence and cannot be changed by automatic repair.", reason=reason, strategy="protected")
            continue
        existing = target.read_bytes()
        base_sha256 = sha256_bytes(existing)
        search = repair.get("search")
        replace = repair.get("replace")
        content = repair.get("content")
        if isinstance(search, str) and isinstance(replace, str):
            operations.append(
                EditOperation(path=rel, kind="replace", search=search, replace=replace, base_sha256=base_sha256, reason=reason)
            )
            metadata.append((rel, search, reason))
        elif isinstance(content, str):
            if len(existing) > DEFAULT_FILE_CHARS:
                fail(
                    rel,
                    "Full-file repairs are disabled when the model saw a truncated source; use an exact SEARCH/REPLACE or patch hunk.",
                    reason=reason,
                )
                continue
            operations.append(
                EditOperation(path=rel, kind="rewrite", content=content, base_sha256=base_sha256, reason=reason)
            )
            metadata.append((rel, None, reason))
        else:
            fail(rel, "Repair missing search/replace, patch, and content.", reason=reason)

    if preflight_failed:
        for rel, attempted_search, reason in metadata:
            results.append(
                ApplyResult(
                    path=rel,
                    ok=False,
                    attempted_search=attempted_search,
                    diagnostics=["Repair transaction aborted during preflight; no files were changed."],
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
            origin="automatic_repair",
            summary="Targeted render repair",
            policy=EditPolicy(protected_paths=frozenset(protected), require_base_for_rewrite=True),
            validate=True,
        )
        committed = commit_transaction(prepared)
    except EditEngineError as exc:
        for rel, attempted_search, reason in metadata:
            results.append(
                ApplyResult(
                    path=rel,
                    ok=False,
                    strategy="conflict" if exc.code == "edit_conflict" else "none",
                    attempted_search=attempted_search,
                    diagnostics=[str(exc)],
                    reason=reason,
                )
            )
        return results

    files = {item.path: item for item in committed.files}
    for rel, attempted_search, reason in metadata:
        item = files.get(rel)
        if item is None:
            continue
        results.append(
            ApplyResult(
                path=rel,
                ok=True,
                strategy=item.strategies[-1] if item.strategies else "none",
                before=_decode_text(item.before),
                after=_decode_text(item.after),
                attempted_search=attempted_search,
                reason=reason,
            )
        )
    return results

def _decode_text(value: bytes | None) -> str | None:
    return value.decode("utf-8", errors="replace") if value is not None else None

def _collect_source_files(
    base: Path,
    failure_text: str,
    *,
    protected_paths: set[str] | None = None,
) -> list[dict[str, str]]:
    files = []
    protected = protected_paths or set()
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.suffix not in TEXT_EXTENSIONS:
            continue
        relative = path.relative_to(base).as_posix()
        if relative in protected:
            continue
        if any(part.startswith(".") for part in path.relative_to(base).parts):
            continue
        content = path.read_text(errors="replace")
        is_referenced = relative in failure_text or Path(relative).name in failure_text
        limit = REFERENCED_FILE_CHARS if is_referenced else DEFAULT_FILE_CHARS
        files.append({"path": relative, "content": content[:limit], "truncated": len(content) > limit, "referenced": str(is_referenced).lower()})

    return sorted(files, key=lambda source_file: (source_file["referenced"] != "true", source_file["path"]))


def _build_repair_prompt(
    source_files: list[dict[str, str]],
    failure_result: dict[str, Any],
    repair_history: list[dict[str, Any]] | None = None,
) -> str:
    context_parts = []
    total_chars = 0
    for source_file in source_files:
        completeness = "truncated; targeted edits only" if source_file.get("truncated") else "complete"
        block = f"### {source_file['path']} ({completeness})\n```\n{source_file['content']}\n```"
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
      "search": "exact snippet to replace (or use patch/content)",
      "replace": "new snippet",
      "reason": "why this repair is required"
    }}
  ]
}}

Rules:
- Only include files shown in Generated Source Files.
- Never edit a ReportPack manifest, execution contract, or validator. Repair upstream source instead.
- If the traceback names a .qmd or .R file, repair that file first.
- Keep repairs minimal and render-oriented. A truncated source excerpt may only receive targeted SEARCH/REPLACE or patch edits, never a full rewrite.
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


def _automatic_repair_protected_paths(base: Path) -> set[str]:
    protected = {CONTRACT_NAME, PACK_SNAPSHOT_NAME}
    try:
        contract = load_execution_contract(base)
    except ExecutionContractError:
        # A malformed/missing required contract is itself protected evidence;
        # automatic source repair must not manufacture a replacement.
        return protected
    if contract is not None:
        protected.update(
            step.path for step in contract.steps if step.role == "validator"
        )
    return protected
