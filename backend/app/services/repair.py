"""Repair service for generated R/Quarto projects after render failures."""

from __future__ import annotations

import json
import logging
from dataclasses import replace as dataclass_replace
from pathlib import Path
from typing import Any

from app.services.apply_edits import ApplyResult
from app.services.agent_failures import diagnose_repair_failure
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
REFERENCED_FILE_CHARS = 28000
MAX_CONTEXT_CHARS = 28000
MAX_REPAIR_REFLECTIONS = 0


async def repair_generated_project(
    project_dir: str,
    failure_result: dict[str, Any],
    repair_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Apply one bounded semantic repair plan after deterministic failure routing."""
    base = Path(project_dir).resolve()
    diagnosis = diagnose_repair_failure(failure_result)
    diagnosis_payload = diagnosis.to_dict()
    if not diagnosis.repairable:
        return {
            "status": "skipped",
            "reason": diagnosis.reason,
            "diagnosis": diagnosis_payload,
            "apply_results": [],
        }

    failure_text = json.dumps(failure_result, default=str)
    protected_paths = _automatic_repair_protected_paths(base)
    source_files = _collect_source_files(
        base,
        failure_text,
        protected_paths=protected_paths,
        diagnosis=diagnosis_payload,
    )
    if not source_files:
        return {
            "status": "skipped",
            "reason": "No bounded generated source context was available for this failure.",
            "diagnosis": diagnosis_payload,
            "apply_results": [],
        }

    system_prompt = """You are a precise semantic repair agent for generated R and Quarto projects.
Return only valid JSON. The runtime, not the model, performs edit targeting.
You may only repair files present in the supplied source context.
Do not invent input files, metadata columns, comparison groups, or biological conclusions.
ReportPack execution contracts, pack manifests, and validator scripts are protected evidence.
Never edit or weaken them. For validator failures, repair an upstream source file only.
Return one or more contiguous line replacements. Each replacement must include:
path, line, optional end_line, diagnosis, replacement, and base_sha256.
The replacement is raw source text without line-number prefixes.
Do not return SEARCH/REPLACE, patch envelopes, full-file content, or fuzzy context.
The runtime verifies the hash, validates the line range, and commits one transaction.
If the failure is not semantically repairable, return an empty repairs array and explain why.
"""
    user_prompt = _build_repair_prompt(
        source_files,
        failure_result,
        repair_history,
        diagnosis=diagnosis_payload,
    )
    response = await call_llm(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_format="json",
        max_tokens=6000,
    )
    repair_plan = _parse_repair_response(response)
    repairs = repair_plan.get("repairs", [])
    if not isinstance(repairs, list) or not repairs:
        return {
            "status": "skipped",
            "reason": repair_plan.get("reason") or "Repair agent returned no line-targeted edits.",
            "diagnosis": diagnosis_payload,
            "raw_response": response[:2000],
            "apply_results": [],
        }

    if repair_history:
        for past_pass in repair_history:
            past_repairs = past_pass.get("repair", {}).get("repairs", [])
            if repairs == past_repairs:
                return {
                    "status": "skipped",
                    "reason": "Repair agent returned an identical repair pass to a previous failed edit.",
                    "diagnosis": diagnosis_payload,
                    "raw_response": response[:2000],
                    "apply_results": [],
                }

    reason = str(repair_plan.get("reason") or "Targeted semantic repair")[:1000]
    apply_results = _apply_line_repairs(
        base,
        repairs,
        protected_paths=protected_paths,
    )
    applied = [item for item in apply_results if item.ok]
    if not applied:
        return {
            "status": "skipped",
            "reason": "The line-targeted repair was not applied; no second model call was made.",
            "diagnosis": diagnosis_payload,
            "plan_reason": reason,
            "raw_response": response[:2000],
            "apply_results": [item.to_dict() for item in apply_results],
        }

    return {
        "status": "repaired",
        "diagnosis": diagnosis_payload,
        "repairs": [
            {
                "path": item.path,
                "reason": item.reason or reason,
                "strategy": item.strategy,
            }
            for item in applied
        ],
        "apply_results": [item.to_dict() for item in apply_results],
    }


def _apply_line_repairs(
    base: Path,
    repairs: list[Any],
    *,
    protected_paths: set[str] | None = None,
) -> list[ApplyResult]:
    """Apply model-proposed line ranges with deterministic runtime targeting."""
    protected = protected_paths or set()
    results: list[ApplyResult] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    source_text: dict[str, str] = {}
    source_bytes: dict[str, bytes] = {}
    preflight_failed = False
    valid_items: list[dict[str, Any]] = []

    def fail(
        path: str,
        message: str,
        *,
        reason: str,
        strategy: str = "none",
        attempted_search: str | None = None,
    ) -> None:
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

    def positive_integer(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    for repair in repairs:
        if not isinstance(repair, dict):
            fail("", "A line repair must be an object.", reason="invalid_operation")
            continue
        relative_path = str(repair.get("path") or "").strip()
        reason = str(
            repair.get("diagnosis") or repair.get("reason") or "Targeted semantic repair"
        )[:1000]
        if not relative_path:
            fail("", "A line repair path is required.", reason=reason)
            continue
        target = _safe_source_path(base, relative_path)
        if target is None or not target.is_file():
            fail(relative_path, "Unsafe or missing repair target.", reason=reason)
            continue
        rel = target.relative_to(base).as_posix()
        line = positive_integer(repair.get("line"))
        end_line = positive_integer(repair.get("end_line")) or line
        attempted = f"lines {line}-{end_line}" if line is not None else None
        if rel in protected:
            fail(
                rel,
                f"{rel} is scientific assurance evidence and cannot be changed by automatic repair.",
                reason=reason,
                strategy="protected",
                attempted_search=attempted,
            )
            continue
        if line is None or end_line is None:
            fail(
                rel,
                "Line-targeted repairs require a positive 1-based line and optional end_line.",
                reason=reason,
                attempted_search=attempted,
            )
            continue
        if end_line < line:
            fail(rel, "end_line must be greater than or equal to line.", reason=reason, attempted_search=attempted)
            continue
        if end_line - line + 1 > 200:
            fail(rel, "A line repair may span at most 200 lines.", reason=reason, attempted_search=attempted)
            continue
        replacement = repair.get("replacement")
        if not isinstance(replacement, str):
            fail(rel, "Line repairs require a string replacement.", reason=reason, attempted_search=attempted)
            continue
        if len(replacement) > 100_000:
            fail(rel, "The replacement exceeds the bounded repair size.", reason=reason, attempted_search=attempted)
            continue
        supplied_hash = repair.get("base_sha256")
        existing = target.read_bytes()
        actual_hash = sha256_bytes(existing)
        if not isinstance(supplied_hash, str) or supplied_hash != actual_hash:
            fail(
                rel,
                "The model must provide the current base_sha256; the file changed or the hash is missing.",
                reason=reason,
                strategy="conflict",
                attempted_search=attempted,
            )
            continue
        try:
            text = existing.decode("utf-8")
        except UnicodeDecodeError:
            fail(rel, "Line-targeted repair requires UTF-8 source text.", reason=reason, attempted_search=attempted)
            continue
        lines = text.splitlines(keepends=True)
        if line > len(lines) or end_line > len(lines):
            fail(
                rel,
                f"Line range {line}-{end_line} is outside the current file ({len(lines)} lines).",
                reason=reason,
                attempted_search=attempted,
            )
            continue
        item: dict[str, Any] = {
            "path": rel,
            "line": line,
            "end_line": end_line,
            "replacement": replacement,
            "reason": reason,
            "attempted_search": attempted,
        }
        grouped.setdefault(rel, []).append(item)
        source_text[rel] = text
        source_bytes[rel] = existing
        valid_items.append(item)

    operations: list[EditOperation] = []
    operation_items: dict[str, list[dict[str, Any]]] = {}
    updated_text: dict[str, str] = {}
    for rel, items in grouped.items():
        ordered: list[dict[str, Any]] = sorted(items, key=lambda item: (item["line"], item["end_line"]))
        previous_end = 0
        overlaps = False
        for item in ordered:
            if item["line"] <= previous_end:
                overlaps = True
                break
            previous_end = item["end_line"]
        if overlaps:
            preflight_failed = True
            for item in ordered:
                results.append(
                    ApplyResult(
                        path=rel,
                        ok=False,
                        attempted_search=item["attempted_search"],
                        diagnostics=["Line ranges overlap; the transaction was not applied."],
                        reason="overlapping_ranges",
                    )
                )
            continue

        original = source_text[rel]
        candidate = original
        newline = "\r\n" if "\r\n" in original else "\n"
        for item in sorted(ordered, key=lambda value: value["line"], reverse=True):
            current_lines = candidate.splitlines(keepends=True)
            start = item["line"] - 1
            end = item["end_line"]
            replacement = item["replacement"]
            target_has_newline = bool(current_lines[end - 1].endswith(("\n", "\r")))
            if replacement and not replacement.endswith(("\n", "\r")) and target_has_newline:
                replacement += newline
            replacement_lines = replacement.splitlines(keepends=True) if replacement else []
            candidate = "".join(current_lines[:start] + replacement_lines + current_lines[end:])
        if candidate == original:
            preflight_failed = True
            for item in ordered:
                results.append(
                    ApplyResult(
                        path=rel,
                        ok=False,
                        attempted_search=item["attempted_search"],
                        diagnostics=["The line replacement is a no-op."],
                        reason="no_op",
                    )
                )
            continue
        updated_text[rel] = candidate
        operation_items[rel] = ordered
        operations.append(
            EditOperation(
                path=rel,
                kind="rewrite",
                content=candidate,
                base_sha256=sha256_bytes(source_bytes[rel]),
                reason="; ".join(item["reason"] for item in ordered),
            )
        )

    if preflight_failed:
        for item in valid_items:
            results.append(
                ApplyResult(
                    path=item["path"],
                    ok=False,
                    attempted_search=item["attempted_search"],
                    diagnostics=["Repair transaction aborted during line-target preflight; no files were changed."],
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
            summary="Bounded line-targeted semantic repair",
            policy=EditPolicy(
                protected_paths=frozenset(protected),
                require_base_for_rewrite=True,
            ),
            validate=True,
        )
        committed = commit_transaction(prepared)
    except EditEngineError as exc:
        for rel, items in operation_items.items():
            for item in items:
                results.append(
                    ApplyResult(
                        path=rel,
                        ok=False,
                        strategy="conflict" if exc.code == "edit_conflict" else "none",
                        attempted_search=item["attempted_search"],
                        diagnostics=[str(exc)],
                        reason=item["reason"],
                    )
                )
        return results

    for rel, items in operation_items.items():
        for item in items:
            results.append(
                ApplyResult(
                    path=rel,
                    ok=True,
                    strategy="line_replace",
                    before=source_text[rel],
                    after=updated_text[rel],
                    attempted_search=item["attempted_search"],
                    reason=item["reason"],
                )
            )
    return results


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
    diagnosis: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Collect only the bounded source context selected by deterministic routing."""
    files: list[dict[str, Any]] = []
    protected = protected_paths or set()
    active_diagnosis = diagnosis or {}
    references = [
        item
        for item in active_diagnosis.get("file_references", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    ]
    context_mode = str(active_diagnosis.get("context_mode") or "relevant_source")

    def matches(relative: str, reference: dict[str, Any]) -> bool:
        reference_path = str(reference.get("path") or "").replace("\\", "/").lstrip("./")
        return (
            relative == reference_path
            or relative.endswith("/" + reference_path)
            or Path(relative).name == Path(reference_path).name
        )

    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.suffix not in TEXT_EXTENSIONS:
            continue
        relative = path.relative_to(base).as_posix()
        if relative in protected or any(part.startswith(".") for part in path.relative_to(base).parts):
            continue
        matching_references = [reference for reference in references if matches(relative, reference)]
        is_referenced = bool(matching_references)
        if references and context_mode in {"line_window", "referenced_file"} and not is_referenced:
            continue

        full_content = path.read_text(errors="replace")
        line_start: int | None = None
        line_end: int | None = None
        if context_mode == "line_window" and matching_references:
            numbered = full_content.splitlines()
            line_numbers = [
                int(reference["line"])
                for reference in matching_references
                if isinstance(reference.get("line"), int)
            ]
            if line_numbers and numbered:
                line_start = max(1, min(line_numbers) - 24)
                requested_end = max(
                    int(reference.get("end_line") or reference["line"])
                    for reference in matching_references
                    if isinstance(reference.get("line"), int)
                )
                line_end = min(len(numbered), requested_end + 24)
                excerpt = numbered[line_start - 1 : line_end]
                content = "\n".join(
                    f"{number:>5}: {line}"
                    for number, line in enumerate(excerpt, start=line_start)
                )
                truncated = line_start > 1 or line_end < len(numbered)
            else:
                content = full_content[:REFERENCED_FILE_CHARS]
                truncated = len(full_content) > REFERENCED_FILE_CHARS
        else:
            limit = REFERENCED_FILE_CHARS if is_referenced else DEFAULT_FILE_CHARS
            content = full_content[:limit]
            truncated = len(full_content) > limit

        files.append(
            {
                "path": relative,
                "content": content,
                "truncated": truncated,
                "referenced": str(is_referenced).lower(),
                "sha256": sha256_bytes(path.read_bytes()),
                "line_start": line_start,
                "line_end": line_end,
            }
        )

    return sorted(
        files,
        key=lambda source_file: (
            source_file["referenced"] != "true",
            source_file["path"],
        ),
    )




def _build_repair_prompt(
    source_files: list[dict[str, Any]],
    failure_result: dict[str, Any],
    repair_history: list[dict[str, Any]] | None = None,
    *,
    diagnosis: dict[str, Any] | None = None,
) -> str:
    context_parts: list[str] = []
    total_chars = 0
    for source_file in source_files:
        completeness = "truncated; line-targeted edits only" if source_file.get("truncated") else "complete"
        location = ""
        if source_file.get("line_start") is not None:
            location = f"; context lines {source_file['line_start']}-{source_file['line_end']}"
        block = (
            f"### {source_file['path']} (sha256: {source_file.get('sha256', 'unknown')}; "
            f"{completeness}{location})\n~~~text\n{source_file['content']}\n~~~"
        )
        if total_chars + len(block) > MAX_CONTEXT_CHARS:
            break
        context_parts.append(block)
        total_chars += len(block)

    failure_json = sanitize_text(json.dumps(failure_result, indent=2, default=str)[-20000:])
    diagnosis_json = sanitize_text(json.dumps(diagnosis or {}, indent=2, default=str))
    history_context = ""
    if repair_history:
        history_json = sanitize_text(json.dumps(repair_history, indent=2, default=str)[-8000:])
        history_context = (
            "\n\n## Previous Failed Repair Attempts\n"
            "Do NOT repeat these failed edits:\n~~~json\n"
            f"{history_json}\n~~~\n"
        )

    source_context = "\n\n".join(context_parts)
    return f"""Repair this generated R/Quarto project after a classified failure.

## Failure Diagnosis

~~~json
{diagnosis_json}
~~~

## Failure Result

~~~json
{failure_json}
~~~
{history_context}
## Generated Source Context

Line labels in a context window are 1-based source line numbers. Do not include
those labels in a replacement string. The SHA-256 value is the exact file base
hash required by the runtime.

{source_context}

## Required JSON Output

Return exactly this shape:

{{
  "reason": "short semantic diagnosis",
  "repairs": [
    {{
      "path": "code/data.R",
      "line": 91,
      "end_line": 91,
      "diagnosis": "why this source range is wrong",
      "replacement": "raw replacement source without line labels",
      "base_sha256": "64 hexadecimal characters from the source header"
    }}
  ]
}}

Rules:
- Only include files shown in Generated Source Context.
- Use one contiguous 1-based line range per repair, at most 200 lines.
- The runtime performs line targeting; do not return SEARCH/REPLACE, patch, or content.
- Never edit a ReportPack manifest, execution contract, or validator. Repair upstream source instead.
- Keep repairs minimal and render-oriented. A truncated context only permits line replacements.
- If the failure is a missing dependency, missing file, timeout, or infrastructure issue, return no repairs.
- If the error is a missing column or group, infer only from shown code and fail honestly if not knowable.
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
