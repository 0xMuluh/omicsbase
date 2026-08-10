"""Generator service — LLM generates the Quarto project file-by-file."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any, Callable

import yaml

from app.config import settings
from app.schemas.schemas import AnalysisPlan
from app.services.file_inspector import format_file_summary_for_llm
from app.services.generation_checkpoint import (
    GenerationCheckpoint,
    canonical_sha256,
    file_sha256,
)
from app.services.llm import call_llm, load_system_prompt
from app.services.provider_errors import LLMProviderError
from app.services.report_pack import (
    MANIFEST_NAME,
    ReportPack,
    ReportPackError,
    ReportPackFile,
    load_report_pack,
    validate_source_closure,
)

logger = logging.getLogger(__name__)


GENERATOR_CHECKPOINT_VERSION = "report-pack-generator-v2"


class AdaptationResponseError(ValueError):
    """Raised when a model response cannot be interpreted as an edit decision."""


class AdaptationGateError(RuntimeError):
    """Raised when a study-bearing exemplar file was not safely adapted."""


class GenerationQualityError(RuntimeError):
    """Raised when deterministic project QA reports blocking errors."""


@dataclass(frozen=True)
class NoChangeDecision:
    """An explicit model decision that an inspected file needs no adaptation."""

    reason: str
    evidence: tuple[str, ...] = ()
    inspected_chunks: int = 1
    content_sha256: str = ""
    adaptation: str = "preserve"


@dataclass(frozen=True)
class AdaptationEdits:
    """Strict targeted edits produced after every source chunk was inspected."""

    edits: tuple[dict[str, str], ...]
    inspected_chunks: int
    content_sha256: str
    adaptation: str = "replace"


@dataclass(frozen=True)
class DeleteDecision:
    """A whole-file deletion decision made after complete source inspection."""

    inspected_chunks: int
    content_sha256: str


ADAPTATION_ACTIONS = {"preserve", "parameterize", "extend", "replace"}

MAX_ADAPT_CHUNK_CHARS = 28_000
MAX_CONTEXT_CHARS = 28_000


# The generation order: each step produces one file, and subsequent steps
# see all previously generated files as context.
GENERATION_STEPS = [
    {"id": "scaffold", "filename": "README.md", "prompt_name": None, "label": "Creating project scaffold"},
    {"id": "spawn", "filename": None, "prompt_name": None, "label": "Spawning report surface skeleton"},
    {"id": "index_qmd", "filename": "code/index.qmd", "prompt_name": None, "label": "Creating report entry page"},
    {"id": "data_r", "filename": "code/data.R", "prompt_name": "generator_data", "label": "Generating data.R"},
    {"id": "funct_r", "filename": "code/funct.R", "prompt_name": "generator_functions", "label": "Generating funct.R"},
    {"id": "qmd_pages", "filename": None, "prompt_name": "generator_qmd", "label": "Generating analysis pages"},
    {"id": "report_fill", "filename": None, "prompt_name": None, "label": "Filling spawned report pages"},
    {"id": "qa_gate", "filename": None, "prompt_name": None, "label": "Checking presentation directives"},
    {"id": "quarto_yml", "filename": "code/_quarto.yml", "prompt_name": "generator_quarto_yml", "label": "Generating _quarto.yml"},
    {"id": "main_r", "filename": "code/main.R", "prompt_name": "generator_main", "label": "Generating main.R"},
    {"id": "readme", "filename": "README.md", "prompt_name": "generator_readme", "label": "Generating README.md"},
]


GENERATION_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "scaffold": (),
    "spawn": ("scaffold",),
    "index_qmd": ("scaffold", "spawn"),
    "data_r": ("spawn",),
    "funct_r": ("data_r",),
    "qmd_pages": ("index_qmd", "data_r", "funct_r"),
    "report_fill": ("spawn", "qmd_pages"),
    "qa_gate": ("qmd_pages", "report_fill"),
    "quarto_yml": ("qmd_pages", "report_fill"),
    "main_r": ("data_r", "funct_r", "qmd_pages"),
    "readme": ("scaffold", "qmd_pages", "report_fill"),
}


def _dependency_paths_for_step(step_id: str, generated_files: dict[str, str]) -> set[str]:
    prefixes = set(GENERATION_DEPENDENCIES.get(step_id, ()))
    if not prefixes:
        return set()
    selected: set[str] = set()
    for path in generated_files:
        if "data_r" in prefixes and path == "code/data.R":
            selected.add(path)
        if "funct_r" in prefixes and path == "code/funct.R":
            selected.add(path)
        if "index_qmd" in prefixes and path == "code/index.qmd":
            selected.add(path)
        if "spawn" in prefixes and path not in {"code/data.R", "code/funct.R", "code/index.qmd"}:
            if path.endswith((".qmd", ".R", ".r", ".yml", ".yaml")):
                selected.add(path)
        if "report_fill" in prefixes and path.endswith((".qmd", ".rmd")):
            selected.add(path)
        if "scaffold" in prefixes and path in {"README.md", "code/design/study_overview.qmd", "code/design/analysis_plan.qmd"}:
            selected.add(path)
    return selected


def _dependency_inputs(generated_files: dict[str, str], paths: set[str]) -> dict[str, str]:
    return {
        path: _content_hash(generated_files[path])
        for path in sorted(paths)
        if path in generated_files
    }


def _adaptation_context_paths(kind: str, relative_path: str, generated_files: dict[str, str]) -> set[str]:
    """Return only the files needed to adapt one source unit."""
    if kind == "page":
        wanted = {"code/data.R", "code/funct.R", "code/index.qmd", "code/_quarto.yml", "code/design/analysis_plan.qmd"}
    elif kind == "config":
        wanted = {path for path in generated_files if path.endswith((".qmd", ".rmd"))}
    else:
        wanted = {"code/data.R", "code/funct.R", "code/index.qmd"}
    wanted.discard(relative_path)
    selected = {path for path in wanted if path in generated_files}
    return selected or {path for path in generated_files if path != relative_path and path in {"code/data.R", "code/funct.R"}}


_ADAPT_EDIT_COMMON = """The file `{relative_path}` in the project is a complete, working template copied from templates/. The rest of the project depends on it: its structure, objects, helper functions, artifact names, and construction approach.

Adapt it to the current study by returning ONLY targeted SEARCH/REPLACE edits:
- First classify the adaptation as one of: `preserve` (template is already correct), `parameterize` (substitute study-specific values or paths), `extend` (add a bounded study-specific section while retaining the template), or `replace` (only when the template structure cannot express the requested analysis; still return targeted edits).
- Identify what this study changes in this file: input paths, column names, factor levels, grouping variables, comparisons, and study-specific narrative. Never change anything else.
- Each edit has an exact `search` block — verbatim lines from the current file — and its `replace`.
- Never rewrite the file: anything you do not edit stays exactly as the template has it.
- Do not rename objects, artifact files, helper functions, or the data-construction approach; the rest of the project loads them.
- Write in the template's voice — a careful analyst reporting the study. No "This page...", no workflow/method meta-commentary, no filler.
- Do not leave template study references (oat, rice, prenatal, FOPP, linderborg, child serum, Muluh, or any copied visit/diet/cohort detail) in any text you touch.
- Keep sections whose source artifacts do not exist yet, saying what artifact is missing.

Return ONLY one of:
- Classified edits: {{"adaptation":"parameterize|extend|replace","edits":[{{"search":"...","replace":"..."}}],"reason":"..."}}
- Classified preservation: {{"adaptation":"preserve","decision":"no_change","reason":"specific conclusion","evidence":["concrete file/plan/manifest fact"]}}
- Backwards-compatible JSON edits: [{{"search": "...", "replace": "..."}}]
- Exactly DELETE when an irrelevant report page should be removed.
"""

_ADAPT_PAGE_INSTRUCTION = _ADAPT_EDIT_COMMON
_ADAPT_SCRIPT_INSTRUCTION = _ADAPT_EDIT_COMMON

_ADAPT_CONFIG_INSTRUCTION = """The file `{relative_path}` is the exemplar's Quarto site configuration. Return ONLY targeted SEARCH/REPLACE edits to adapt it if needed: keep the site structure and navigation style; ensure render entries and navigation reflect the actual files in the project; never rewrite the file.

Return ONLY JSON edits, or an explicit no-change decision with a specific reason:
[{{"search": "...", "replace": "..."}}]
{{"decision":"no_change","reason":"specific conclusion","evidence":["concrete file/plan/manifest fact"]}}
"""


def _adapt_instruction_for(kind: str, relative_path: str) -> str:
    if kind == "script":
        return _ADAPT_SCRIPT_INSTRUCTION.format(relative_path=relative_path)
    if kind == "config":
        return _ADAPT_CONFIG_INSTRUCTION.format(relative_path=relative_path)
    return _ADAPT_PAGE_INSTRUCTION.format(relative_path=relative_path)


async def _request_file_edits_chunk(
    instruction: str,
    file_content: str,
    *,
    system_prompt: str,
    plan_json: str,
    file_descriptions: str,
    uploaded_file_paths: dict[str, list[str]],
    target_file: str,
    generated_context: dict[str, str],
    study_manifest_json: str = "{}",
    context_paths: set[str] | None = None,
) -> AdaptationEdits | str | NoChangeDecision:
    """Ask the model for edits to one fully visible source chunk.

    Returns classified edits, DELETE, or an explicit evidence-bearing no-change
    decision. An empty edit array is rejected because it cannot distinguish
    inspection from provider/parser failure.
    """
    user_prompt = f"""## Task

The current file `{target_file}` is below. Adapt it with targeted SEARCH/REPLACE edits.

{instruction}

## Adaptation classification

Before editing, classify this file as exactly one of `preserve`, `parameterize`, `extend`, or `replace`. Use `preserve` only when the template is already correct and return an evidence-bearing `no_change` decision. Use `parameterize` for study-specific values or paths, `extend` for a bounded addition that retains the template structure, and `replace` only when the structure cannot express the requested analysis. Classified edits must include the chosen `adaptation` value.

## Current file: {target_file}

```text
{file_content[:40000]}
```

## Analysis Plan

```json
{plan_json}
```

## Uploaded Data Files

{file_descriptions}

## Validated Study Manifest

```json
{study_manifest_json}
```

## Project Data Paths (relative to code/)

{_format_data_path_mapping(uploaded_file_paths)}

## Previously Generated Files

{_build_context_text(generated_context, exclude=target_file, include=context_paths)}

## Output

Return ONLY the JSON edits, explicit no-change object, or exactly DELETE.
Never return an empty array. No markdown fences, no explanation.
"""
    response = await call_llm(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=6000,
    )
    stripped = response.strip()
    if stripped.upper() == "DELETE":
        return "DELETE"
    if stripped.startswith("```"):
        first_newline = stripped.index("\n")
        stripped = stripped[first_newline + 1:]
        if stripped.endswith("```"):
            stripped = stripped[:-3]
        stripped = stripped.strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise AdaptationResponseError(
            f"Adapt edit response for {target_file} was not valid JSON"
        ) from exc
    adaptation_action = "replace"
    if isinstance(data, dict) and "adaptation" in data:
        adaptation_action = str(data.get("adaptation") or "").strip().lower()
        if adaptation_action not in ADAPTATION_ACTIONS:
            raise AdaptationResponseError(
                f"Adapt edit response for {target_file} used unknown adaptation classification"
            )
        if adaptation_action == "preserve":
            if data.get("edits"):
                raise AdaptationResponseError(
                    f"Adapt edit response for {target_file} classified preserve but returned edits"
                )
            data = {
                "decision": "no_change",
                "reason": data.get("reason"),
                "evidence": data.get("evidence"),
            }
        else:
            raw_edits = data.get("edits")
            if not isinstance(raw_edits, list):
                raise AdaptationResponseError(
                    f"Adapt edit response for {target_file} classification requires an edits list"
                )
            data = raw_edits
    if isinstance(data, dict):
        if (
            data.get("decision") == "no_change"
            and set(data).issubset({"decision", "reason", "evidence"})
        ):
            reason = str(data.get("reason") or "").strip()
            raw_evidence = data.get("evidence")
            if (
                len(reason) >= 20
                and isinstance(raw_evidence, list)
                and 1 <= len(raw_evidence) <= 5
                and all(
                    isinstance(item, str) and len(item.strip()) >= 10
                    for item in raw_evidence
                )
            ):
                evidence = tuple(item.strip() for item in raw_evidence)
                if len(evidence) == len(set(evidence)):
                    return NoChangeDecision(
                        reason=reason,
                        evidence=evidence,
                        adaptation="preserve",
                    )
        raise AdaptationResponseError(
            f"Adapt edit response for {target_file} contained an invalid decision object"
        )
    if not isinstance(data, list):
        raise AdaptationResponseError(
            f"Adapt edit response for {target_file} must be a JSON array"
        )
    edits: list[dict[str, str]] = []
    for index, item in enumerate(data, start=1):
        if (
            not isinstance(item, dict)
            or set(item) != {"search", "replace"}
            or not isinstance(item.get("search"), str)
            or not item["search"]
            or not isinstance(item.get("replace"), str)
        ):
            raise AdaptationResponseError(
                f"Adapt edit response for {target_file} had malformed edit item {index}"
            )
        edits.append({"search": item["search"], "replace": item["replace"]})
    if not edits:
        raise AdaptationResponseError(
            f"Adapt edit response for {target_file} was empty; return an explicit no_change decision"
        )
    return AdaptationEdits(
        edits=tuple(edits),
        inspected_chunks=1,
        content_sha256=_content_hash(file_content),
        adaptation=adaptation_action,
    )


def _split_source_chunks(
    content: str,
    *,
    max_chars: int = MAX_ADAPT_CHUNK_CHARS,
) -> list[tuple[int, int, str]]:
    """Split on line boundaries while covering every source character once."""
    if not content:
        return [(0, 0, "")]
    chunks: list[tuple[int, int, str]] = []
    start = 0
    while start < len(content):
        proposed_end = min(start + max_chars, len(content))
        end = proposed_end
        if proposed_end < len(content):
            line_end = content.rfind("\n", start, proposed_end)
            if line_end > start:
                end = line_end + 1
        chunks.append((start, end, content[start:end]))
        start = end
    return chunks


async def _request_file_edits(
    instruction: str,
    file_content: str,
    *,
    system_prompt: str,
    plan_json: str,
    file_descriptions: str,
    uploaded_file_paths: dict[str, list[str]],
    target_file: str,
    generated_context: dict[str, str],
    study_manifest_json: str = "{}",
    context_paths: set[str] | None = None,
) -> AdaptationEdits | DeleteDecision | NoChangeDecision:
    """Inspect the complete file, requiring a valid decision for every chunk."""
    chunks = _split_source_chunks(file_content)
    content_sha256 = _content_hash(file_content)
    decisions: list[tuple[AdaptationEdits | str | NoChangeDecision, str]] = []
    for chunk_index, (start, end, chunk) in enumerate(chunks, start=1):
        chunk_instruction = (
            instruction
            + "\n\n## Required source coverage\n\n"
            + f"You are inspecting chunk {chunk_index} of {len(chunks)}, "
            + f"character range [{start}, {end}), from a file with SHA-256 "
            + f"{content_sha256}. SEARCH blocks must occur inside this chunk. "
            + "Your decision must cover this entire chunk."
        )
        decision = await _request_file_edits_chunk(
            chunk_instruction,
            chunk,
            system_prompt=system_prompt,
            plan_json=plan_json,
            file_descriptions=file_descriptions,
            uploaded_file_paths=uploaded_file_paths,
            target_file=target_file,
            generated_context=generated_context,
            study_manifest_json=study_manifest_json,
            context_paths=context_paths,
        )
        if isinstance(decision, AdaptationEdits):
            for edit in decision.edits:
                if edit["search"] not in chunk:
                    raise AdaptationResponseError(
                        f"Adapt edit response for {target_file} chunk {chunk_index} "
                        "contained a SEARCH block outside the inspected chunk"
                    )
        decisions.append((decision, chunk))

    delete_count = sum(decision == "DELETE" for decision, _ in decisions)
    if delete_count:
        if delete_count != len(decisions):
            raise AdaptationResponseError(
                f"Adapt edit response for {target_file} mixed DELETE with chunk decisions"
            )
        return DeleteDecision(
            inspected_chunks=len(chunks),
            content_sha256=content_sha256,
        )

    edit_decisions = [
        decision
        for decision, _ in decisions
        if isinstance(decision, AdaptationEdits)
    ]
    edits = tuple(edit for decision in edit_decisions for edit in decision.edits)
    if edits:
        classifications = {decision.adaptation for decision in edit_decisions}
        if len(classifications) > 1:
            raise AdaptationResponseError(
                f"Adapt edit response for {target_file} used conflicting adaptation classifications across chunks"
            )
        return AdaptationEdits(
            edits=edits,
            inspected_chunks=len(chunks),
            content_sha256=content_sha256,
            adaptation=next(iter(classifications), "replace"),
        )

    no_changes = [
        decision
        for decision, _ in decisions
        if isinstance(decision, NoChangeDecision)
    ]
    if len(no_changes) == len(chunks):
        reason = "; ".join(
            f"chunk {index}: {decision.reason[:400]}"
            for index, decision in enumerate(no_changes, start=1)
        )
        return NoChangeDecision(
            reason=reason,
            evidence=tuple(
                f"chunk {index}: {item}"
                for index, decision in enumerate(no_changes, start=1)
                for item in decision.evidence
            ),
            inspected_chunks=len(chunks),
            content_sha256=content_sha256,
            adaptation="preserve",
        )
    raise AdaptationResponseError(
        f"Adapt edit response for {target_file} did not cover every source chunk"
    )


def _build_context_text(
    generated_context: dict[str, str],
    exclude: str | None = None,
    *,
    max_chars: int = MAX_CONTEXT_CHARS,
    include: set[str] | None = None,
) -> str:
    available = {
        path: content
        for path, content in generated_context.items()
        if (not exclude or path != exclude) and (include is None or path in include)
    }
    if not available:
        return "(No other files yet)"
    inventory = "\n".join(
        f"- {path} ({len(content)} characters)"
        for path, content in sorted(available.items())
    )
    parts = [f"### Complete project file inventory\n{inventory}"]
    used = len(parts[0])
    for path, content in sorted(available.items()):
        remaining = max_chars - used
        if remaining < 300:
            break
        excerpt = content[: min(4_000, remaining - 100)]
        part = f"### {path}\n~~~\n{excerpt}\n~~~"
        parts.append(part)
        used += len(part)
    return "\n\n".join(parts)


def _format_data_path_mapping(
    uploaded_file_paths: dict[str, list[str]],
) -> str:
    """Expose project-relative bindings without leaking private host paths."""
    rows = [
        f"- {role}[{index}]: ../data/{Path(path).name}"
        for role, paths in sorted(uploaded_file_paths.items())
        for index, path in enumerate(paths, start=1)
    ]
    return "\n".join(rows) if rows else "(No uploaded file bindings.)"


def _apply_edits_with_report(
    whole: str,
    edits: list[dict],
    path: str,
) -> tuple[str, int, list[str]]:
    """Apply a file's adaptation edits as an all-or-nothing in-memory unit.

    A rejected or malformed edit returns the original content. This keeps the
    adaptation checkpoint honest: a partially adapted file is never written or
    later mistaken for a user-owned result.
    """
    from app.services.edit_engine import safe_replace_text

    file_length = len(whole)
    rewrite_threshold = int(file_length * 0.6)
    updated = whole
    applied = 0
    rejected: list[str] = []
    for edit in edits:
        if not isinstance(edit, dict):
            rejected.append("edit must be an object")
            break
        search = edit.get("search")
        replace = edit.get("replace")
        if not isinstance(search, str) or not isinstance(replace, str):
            rejected.append("edit requires string SEARCH and REPLACE blocks")
            break
        if not search:
            rejected.append("empty SEARCH block")
            break
        if len(search) > rewrite_threshold or len(replace) > rewrite_threshold:
            rejected.append(
                f"rewrite-sized edit rejected: SEARCH={len(search)}B "
                f"REPLACE={len(replace)}B file={file_length}B"
            )
            logger.warning(
                "Adapt edit rejected for %s: SEARCH=%dB REPLACE=%dB (file=%dB) — rewrite in disguise",
                path,
                len(search),
                len(replace),
                file_length,
            )
            break
        ok, candidate, strategy, diagnostic = safe_replace_text(updated, search, replace)
        if not ok:
            diagnostic = diagnostic or "SEARCH block did not match exactly one location."
            rejected.append(str(diagnostic))
            logger.warning("Adapt edit did not match in %s (%s): %s", path, strategy, diagnostic)
            break
        updated = candidate
        applied += 1
    if rejected:
        return whole, 0, rejected
    return updated, applied, []

def _apply_edits_to_file(whole: str, edits: list[dict], path: str) -> str:
    """Compatibility wrapper returning only the edited content."""
    updated, _, _ = _apply_edits_with_report(whole, edits, path)
    return updated


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()


def _generation_run_inputs(
    *,
    base: Path,
    plan: AnalysisPlan,
    file_summaries: list[dict],
    uploaded_file_paths: dict[str, list[str]],
    study_manifest: dict[str, Any] | None,
    report_pack: ReportPack,
    system_prompt: str,
) -> dict[str, Any]:
    """Build the non-secret, content-addressed generation input manifest."""
    uploads: list[dict[str, Any]] = []
    for role, paths in sorted(uploaded_file_paths.items()):
        for index, raw_path in enumerate(paths, start=1):
            source = Path(raw_path)
            uploads.append(
                {
                    "role": role,
                    "index": index,
                    "name": source.name,
                    "size": source.stat().st_size if source.is_file() else None,
                    "sha256": file_sha256(source),
                }
            )
    pack_identity = report_pack.metadata()
    if report_pack.source == "discovered" and report_pack.root == base.resolve():
        # A project-local discovered pack contains generator outputs. Hashing
        # that mutable tree would make the checkpoint invalidate itself.
        pack_identity["source_tree_sha256"] = "project-local-discovered"
    return {
        "generator_version": GENERATOR_CHECKPOINT_VERSION,
        "plan_sha256": canonical_sha256(plan.model_dump()),
        "study_manifest_sha256": canonical_sha256(study_manifest or {}),
        "file_summaries_sha256": canonical_sha256(file_summaries),
        "uploads": uploads,
        "report_pack": pack_identity,
        "system_prompt_sha256": _content_hash(system_prompt),
        "llm": {
            "provider": settings.llm_provider,
            "model": settings.llm_model,
        },
    }


async def _gather_fail_fast(*awaitables):
    """Gather in order while cancelling unfinished siblings on first error."""
    tasks = [asyncio.create_task(awaitable) for awaitable in awaitables]
    if not tasks:
        return []
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in done:
            exception = task.exception()
            if exception is not None:
                raise exception
        return await asyncio.gather(*tasks)
    finally:
        unfinished = [task for task in tasks if not task.done()]
        for task in unfinished:
            task.cancel()
        if unfinished:
            await asyncio.gather(*unfinished, return_exceptions=True)


def _strip_inline_hash_comment(line: str, *, suffix: str) -> str:
    """Remove an R/YAML hash comment without treating quoted hashes as comments."""
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(line):
        char = line[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\" and quote != "`":
                escaped = True
            elif char == quote:
                # YAML escapes a single quote inside a single-quoted scalar by
                # doubling it. Keep scanning inside the scalar in that case.
                if (
                    suffix in {".yml", ".yaml"}
                    and quote == "'"
                    and index + 1 < len(line)
                    and line[index + 1] == "'"
                ):
                    index += 1
                else:
                    quote = None
        elif char in {"'", '"', "`"}:
            quote = char
        elif char == "#" and (
            suffix == ".r" or index == 0 or line[index - 1].isspace()
        ):
            return line[:index]
        index += 1
    return line


def _material_signature(content: str, relative_path: str) -> str:
    """Normalize formatting and non-executable comments for a material-change gate."""
    suffix = Path(relative_path).suffix.lower()
    normalized_lines: list[str] = []
    for line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        executable = (
            _strip_inline_hash_comment(line, suffix=suffix)
            if suffix in {".r", ".yml", ".yaml"}
            else line
        )
        stripped = executable.strip()
        if not stripped:
            continue
        normalized_lines.append(re.sub(r"\s+", " ", stripped))
    return "\n".join(normalized_lines)


def _is_material_adaptation(original: str, updated: str, relative_path: str) -> bool:
    return _material_signature(original, relative_path) != _material_signature(
        updated,
        relative_path,
    )


def _qa_project_relative_path(relative_path: str) -> str:
    normalized = str(relative_path).strip().replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized if normalized.startswith("code/") else f"code/{normalized}"


def _partition_qa_removals(
    relative_paths: list[str],
    classifications: dict[str, ReportPackFile],
) -> tuple[list[str], list[str], list[str]]:
    """Partition QA deletions into allowed, immutable, and required paths."""
    prunable: list[str] = []
    preserved: list[str] = []
    blocked: list[str] = []
    for relative in dict.fromkeys(relative_paths):
        classification = classifications.get(_qa_project_relative_path(relative))
        if classification is not None and classification.adaptation == "none":
            preserved.append(relative)
        elif classification is not None and classification.adaptation == "required":
            blocked.append(relative)
        else:
            prunable.append(relative)
    return prunable, preserved, blocked


def _validate_adapted_source_closure(base: Path, report_pack: ReportPack) -> None:
    """Validate the final generated bytes using the pack's runtime cwd."""
    working_directory = (
        report_pack.execution.working_directory
        if report_pack.execution is not None
        else None
    )
    try:
        validate_source_closure(
            base,
            execution_working_directory=working_directory,
        )
    except ReportPackError as exc:
        raise GenerationQualityError(
            f"Generated source dependency closure is invalid: {exc}"
        ) from exc


def _write_adaptation_manifest(
    base: Path,
    outcomes: list[dict[str, Any]],
    *,
    report_pack: dict[str, Any] | None = None,
) -> Path:
    """Persist truthful per-file adaptation outcomes for review and debugging."""
    counts = Counter(str(item.get("status") or "unknown") for item in outcomes)
    payload = {
        "version": "1.0",
        "summary": dict(sorted(counts.items())),
        "files": sorted(outcomes, key=lambda item: str(item.get("path") or "")),
    }
    if report_pack:
        payload["report_pack"] = report_pack
    target = base / "adaptation_manifest.json"
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return target


def _finalize_adaptation_outcomes(
    base: Path,
    outcomes: list[dict[str, Any]],
) -> None:
    """Reconcile evidence with source bytes after QA repair and pruning."""
    for outcome in outcomes:
        outcome.setdefault("adaptation_status", outcome.get("status"))
        relative = str(outcome.get("path") or "")
        target = base / relative
        if not target.is_file():
            outcome["result_sha256"] = None
            if outcome.get("status") != "deleted":
                outcome["status"] = "removed_by_qa"
            outcome["finalized"] = True
            continue
        final_content = target.read_text(errors="replace")
        final_hash = _content_hash(final_content)
        if (
            outcome.get("result_sha256") is not None
            and outcome.get("result_sha256") != final_hash
        ):
            outcome["status"] = "qa_repaired"
        outcome["result_sha256"] = final_hash
        outcome["finalized"] = True


async def generate_project(
    project_dir: str,
    plan: AnalysisPlan,
    file_summaries: list[dict],
    uploaded_file_paths: dict[str, list[str]],
    study_manifest: dict[str, Any] | None = None,
    progress_callback: Callable[[str, str, dict[str, Any] | None], None] | None = None,
    resume_from_checkpoint: bool = True,
) -> list[str]:
    """Generate the entire Quarto project from an approved analysis plan.

    Args:
        project_dir: Path to the project directory.
        plan: The approved analysis plan.
        file_summaries: File inspection summaries.
        uploaded_file_paths: Map of role → file paths for uploaded files.
        study_manifest: Validated evidence snapshot of the uploaded study.
        progress_callback: Optional callback(step_id, status, metadata) for progress updates.

    Returns:
        List of generated file paths.
    """
    base = Path(project_dir)
    code_dir = base / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    (base / "data").mkdir(exist_ok=True)
    (base / "output").mkdir(exist_ok=True)

    plan_json = plan.model_dump_json(indent=2)
    file_desc = "\n\n".join(format_file_summary_for_llm(s) for s in file_summaries)
    study_manifest_json = json.dumps(
        study_manifest or {},
        indent=2,
        sort_keys=True,
        default=str,
    )

    # Track generated files for context accumulation
    generated_files: dict[str, str] = {}
    generated_paths: list[str] = []

    def _report(step_id: str, status: str, metadata: dict[str, Any] | None = None):
        if progress_callback:
            progress_callback(step_id, status, metadata)

    # Resolve every input to the generation fingerprint before writing source.
    # This lets retries distinguish reusable output from stale output and edits.
    from app.services.spawner import resolve_report_pack, spawn_report_pack

    report_pack = resolve_report_pack(plan.report_pack_id, domain=plan.domain)
    if report_pack is None:
        # A project-local discovered pack has no immutable external source tree.
        # Its mutable tree is deliberately excluded from the run fingerprint.
        report_pack = load_report_pack(base, domain=plan.domain)
    system_prompt = load_system_prompt(
        list(report_pack.prompt_references),
        include_registry=False,
    )
    checkpoint = GenerationCheckpoint(
        base,
        run_inputs=_generation_run_inputs(
            base=base,
            plan=plan,
            file_summaries=file_summaries,
            uploaded_file_paths=uploaded_file_paths,
            study_manifest=study_manifest,
            report_pack=report_pack,
            system_prompt=system_prompt,
        ),
        generator_version=GENERATOR_CHECKPOINT_VERSION,
        resume=resume_from_checkpoint,
    )

    _report("scaffold", "running", {"detail": "Creating project folders and starter files"})
    scaffold_paths = _write_project_scaffold(
        base,
        plan,
        uploaded_file_paths,
        checkpoint=checkpoint,
    )
    for scaffold_path in scaffold_paths:
        relative_path = _relative_project_path(base, scaffold_path)
        generated_paths.append(str(scaffold_path))
        if relative_path.startswith("code/"):
            generated_files[relative_path] = scaffold_path.read_text(errors="replace")
    _report("scaffold", "completed", {"detail": "Starter project is visible", "path": "README.md"})

    # --- Spawn the exemplar project tree (the template IS the report) ---
    _report("spawn", "running", {"detail": "Copying exemplar template project"})
    spawned_files = (
        spawn_report_pack(
            project_dir=project_dir,
            pack=report_pack,
            checkpoint=checkpoint,
        )
        if report_pack.root != base.resolve()
        else {}
    )
    # Scaffold-only files (entry/design pages the exemplar lacks) join the
    # adapt round so they get the house voice too.
    for owned in ("code/index.qmd", "code/design/study_overview.qmd", "code/design/analysis_plan.qmd"):
        target = base / owned
        if target.exists() and owned not in spawned_files:
            spawned_files[owned] = target.read_text(errors="replace")
    pack_snapshot_path: str | None = None
    source_manifest = report_pack.root / MANIFEST_NAME
    if report_pack.source == "declared" and source_manifest.is_file():
        pack_snapshot = base / "report_pack.yaml"
        snapshot_inputs = {"source_sha256": file_sha256(source_manifest)}
        snapshot_decision = checkpoint.decide(
            "pack_snapshot",
            [pack_snapshot.name],
            unit_inputs=snapshot_inputs,
        )
        if snapshot_decision.action == "run":
            pack_snapshot.write_bytes(source_manifest.read_bytes())
            checkpoint.complete(
                "pack_snapshot",
                [pack_snapshot.name],
                unit_inputs=snapshot_inputs,
            )
        elif snapshot_decision.action == "preserve":
            checkpoint.preserve(
                "pack_snapshot",
                [pack_snapshot.name],
                reason=snapshot_decision.reason,
                unit_inputs=snapshot_inputs,
            )
        pack_snapshot_path = pack_snapshot.name
        generated_paths.append(str(pack_snapshot))

    spawn_paths: list[tuple[str, str, str, ReportPackFile]] = []
    for relative_path, content in spawned_files.items():
        abs_path = base / relative_path
        if str(abs_path) not in generated_paths:
            generated_paths.append(str(abs_path))
        generated_files[relative_path] = content
        path_name = Path(relative_path).name.lower()
        suffix = Path(relative_path).suffix.lower()
        if suffix in {".qmd", ".rmd"}:
            kind = "page"
        elif suffix == ".r" and path_name != "main.r":
            kind = "script"
        elif path_name == "_quarto.yml":
            kind = "config"
        else:
            kind = "copy"
        classification = report_pack.classify(relative_path)
        spawn_paths.append((relative_path, content, kind, classification))
    pack_classifications = {
        relative_path: classification
        for relative_path, _, _, classification in spawn_paths
    }
    _report(
        "spawn",
        "completed",
        {"detail": f"Copied {len(spawned_files)} exemplar file(s) into the project"},
    )

    pack_loaders = [
        classification.path
        for _, _, _, classification in spawn_paths
        if classification.role == "data_loader"
    ]
    pack_helpers = [
        classification.path
        for _, _, _, classification in spawn_paths
        if classification.role == "helper"
    ]

    # --- Step 1: data loader (template-provided) ---
    if pack_loaders:
        _report(
            "data_r",
            "completed",
            {
                "detail": "Using report-pack data construction source",
                "path": pack_loaders[0],
                "paths": pack_loaders,
            },
        )
    elif "code/data.R" in generated_files:
        _report("data_r", "completed", {"detail": "Using the exemplar data construction script", "path": "code/data.R"})
    else:
        _report("data_r", "running", {"detail": "Generating data loader", "path": "code/data.R"})
        relative_path = "code/data.R"
        unit_id = "generate:data.R"
        unit_inputs = {"instruction_sha256": _content_hash(_DATA_R_INSTRUCTION)}
        decision = checkpoint.decide(unit_id, [relative_path], unit_inputs=unit_inputs)
        if decision.action == "run":
            try:
                data_r = await _generate_file(
                    system_prompt=system_prompt,
                    plan_json=plan_json,
                    file_descriptions=file_desc,
                    uploaded_file_paths=uploaded_file_paths,
                    target_file="data.R",
                    instruction=_DATA_R_INSTRUCTION,
                    generated_context=generated_files,
                    study_manifest_json=study_manifest_json,
                )
                _write_file(code_dir / "data.R", data_r)
                checkpoint.complete(unit_id, [relative_path], unit_inputs=unit_inputs)
            except Exception as exc:
                checkpoint.fail(unit_id, [relative_path], error=str(exc), unit_inputs=unit_inputs)
                raise
        else:
            if decision.action == "preserve":
                checkpoint.preserve(
                    unit_id,
                    [relative_path],
                    reason=decision.reason,
                    unit_inputs=unit_inputs,
                )
            data_r = (base / relative_path).read_text(errors="replace")
        generated_files["code/data.R"] = data_r
        generated_paths.append(str(code_dir / "data.R"))
        _report("data_r", "completed", {"detail": "Wrote code/data.R", "path": "code/data.R"})

    # --- Step 2: helper source (template-provided) ---
    if pack_helpers:
        _report(
            "funct_r",
            "completed",
            {
                "detail": "Using report-pack helper source",
                "path": pack_helpers[0],
                "paths": pack_helpers,
            },
        )
    elif "code/funct.R" in generated_files:
        _report("funct_r", "completed", {"detail": "Using the exemplar helper functions", "path": "code/funct.R"})
    else:
        _report("funct_r", "running", {"detail": "Generating reusable R helpers", "path": "code/funct.R"})
        relative_path = "code/funct.R"
        unit_id = "generate:funct.R"
        unit_inputs = {"instruction_sha256": _content_hash(_FUNCT_R_INSTRUCTION)}
        decision = checkpoint.decide(unit_id, [relative_path], unit_inputs=unit_inputs)
        if decision.action == "run":
            try:
                funct_r = await _generate_file(
                    system_prompt=system_prompt,
                    plan_json=plan_json,
                    file_descriptions=file_desc,
                    uploaded_file_paths=uploaded_file_paths,
                    target_file="funct.R",
                    instruction=_FUNCT_R_INSTRUCTION,
                    generated_context=generated_files,
                    study_manifest_json=study_manifest_json,
                )
                _write_file(code_dir / "funct.R", funct_r)
                checkpoint.complete(unit_id, [relative_path], unit_inputs=unit_inputs)
            except Exception as exc:
                checkpoint.fail(unit_id, [relative_path], error=str(exc), unit_inputs=unit_inputs)
                raise
        else:
            if decision.action == "preserve":
                checkpoint.preserve(
                    unit_id,
                    [relative_path],
                    reason=decision.reason,
                    unit_inputs=unit_inputs,
                )
            funct_r = (base / relative_path).read_text(errors="replace")
        generated_files["code/funct.R"] = funct_r
        generated_paths.append(str(code_dir / "funct.R"))
        _report("funct_r", "completed", {"detail": "Wrote code/funct.R", "path": "code/funct.R"})

    # --- Step 3: QMD pages (parallelized with asyncio.gather) ---
    # Steps whose page already exists in the spawned exemplar tree are adapted
    # by the adapt stage; only genuinely new analysis pages are generated.
    enabled_steps = [s for s in plan.workflow if s.enabled]

    async def _step_already_spawned(step) -> bool:
        subdir, filename = _step_to_qmd_path(step.id)
        return bool(subdir and f"code/{subdir}/{filename}" in generated_files)

    spawned_steps = [s for s in enabled_steps if await _step_already_spawned(s)]
    new_steps = [s for s in enabled_steps if s not in spawned_steps]
    for step in spawned_steps:
        subdir, filename = _step_to_qmd_path(step.id)
        display_path = f"code/{subdir}/{filename}"
        _report(
            f"qmd_{step.id}",
            "completed",
            {"detail": f"Exemplar page {display_path} will be adapted in place", "path": display_path},
        )

    async def _process_step(step):
        step_id = f"qmd_{step.id}"
        subdir, filename = _step_to_qmd_path(step.id)
        qmd_dir = code_dir / subdir if subdir else code_dir
        qmd_dir.mkdir(parents=True, exist_ok=True)
        qmd_path = qmd_dir / filename
        display_path = _relative_project_path(base, qmd_path)
        _report(step_id, "running", {"detail": f"Generating {display_path}", "path": display_path})

        async def _generate_qmd(
            path: Path,
            target_file: str,
            instruction: str,
            context_paths: set[str] | None = None,
        ) -> tuple[str, str]:
            relative_path = _relative_project_path(base, path)
            dependency_paths = context_paths or _dependency_paths_for_step("qmd_pages", generated_files)
            dependency_hashes = _dependency_inputs(generated_files, dependency_paths)
            unit_id = f"qmd:{relative_path}"
            unit_inputs = {
                "instruction_sha256": _content_hash(instruction),
                "dependency_paths": sorted(dependency_paths),
                "dependency_hashes": dependency_hashes,
            }
            decision = checkpoint.decide(
                unit_id,
                [relative_path],
                unit_inputs=unit_inputs,
            )
            if decision.action == "run":
                try:
                    content = await _generate_file(
                        system_prompt=system_prompt,
                        plan_json=plan_json,
                        file_descriptions=file_desc,
                        uploaded_file_paths=uploaded_file_paths,
                        target_file=target_file,
                        instruction=instruction,
                        generated_context={key: generated_files[key] for key in dependency_paths if key in generated_files},
                        study_manifest_json=study_manifest_json,
                        context_paths=dependency_paths,
                    )
                    _write_file(path, content)
                    checkpoint.complete(
                        unit_id,
                        [relative_path],
                        unit_inputs=unit_inputs,
                    )
                    return relative_path, content
                except BaseException as exc:
                    if isinstance(exc, Exception):
                        checkpoint.fail(
                            unit_id,
                            [relative_path],
                            error=str(exc),
                            unit_inputs=unit_inputs,
                        )
                    raise
            if decision.action == "preserve":
                checkpoint.preserve(
                    unit_id,
                    [relative_path],
                    reason=decision.reason,
                    unit_inputs=unit_inputs,
                )
            return relative_path, (base / relative_path).read_text(errors="replace")

        step_results = []
        if step.classification == "contested" and step.ensemble_methods:
            for method in step.ensemble_methods:
                method_filename = f"{step.id}_{method['id']}.qmd"
                method_path = qmd_dir / method_filename
                method_instruction = _QMD_INSTRUCTION.format(
                    step_name=f"{step.name} — {method['name']}",
                    step_id=step.id,
                    method_id=method["id"],
                    method_name=method["name"],
                    classification=step.classification,
                    extra="This is one method in a contested ensemble. Generate the analysis using ONLY this method.",
                )
                method_relative_path, method_qmd = await _generate_qmd(
                    method_path,
                    method_filename,
                    method_instruction,
                )
                generated_files[method_relative_path] = method_qmd
                step_results.append((step_id, method_relative_path, str(method_path), method_qmd))

            comparison_filename = f"{step.id}_consensus.qmd"
            comparison_path = qmd_dir / comparison_filename
            comparison_instruction = _CONSENSUS_INSTRUCTION.format(
                step_name=step.name,
                step_id=step.id,
                methods=", ".join(m["name"] for m in step.ensemble_methods),
            )
            comparison_relative_path, comparison_qmd = await _generate_qmd(
                comparison_path,
                comparison_filename,
                comparison_instruction,
                context_paths=(
                    _dependency_paths_for_step("qmd_pages", generated_files)
                    | {item[1] for item in step_results}
                ),
            )
            step_results.append((step_id, comparison_relative_path, str(comparison_path), comparison_qmd))
            return step_results
        else:
            instruction = _QMD_INSTRUCTION.format(
                step_name=step.name,
                step_id=step.id,
                method_id="",
                method_name="",
                classification=step.classification,
                extra="",
            )
            display_path, qmd_content = await _generate_qmd(
                qmd_path,
                filename,
                instruction,
            )
            return [(step_id, display_path, str(qmd_path), qmd_content)]

    if new_steps:
        nested_results = await _gather_fail_fast(*[_process_step(s) for s in new_steps])
        for step_results in nested_results:
            for step_id, display_path, abs_path, qmd_content in step_results:
                generated_files[display_path] = qmd_content
                if abs_path not in generated_paths:
                    generated_paths.append(abs_path)
                _report(step_id, "completed", {"detail": f"Finished page {display_path}", "path": display_path})

    adaptation_outcomes: list[dict[str, Any]] = []
    adaptation_manifest: Path | None = None
    pack_metadata: dict[str, Any] | None = None

    # --- Step 3b: fill spawned report surface pages in place ---
    if spawn_paths:
        adaptable_paths = [
            item for item in spawn_paths if item[3].adaptation != "none"
        ]
        _report(
            "report_fill",
            "running",
            {
                "detail": (
                    f"Inspecting {len(adaptable_paths)} of {len(spawn_paths)} "
                    "report-pack file(s) for study adaptation"
                )
            },
        )

        def _base_outcome(
            relative_path: str,
            kind: str,
            classification: ReportPackFile,
        ) -> dict[str, Any]:
            original = generated_files.get(relative_path, "")
            return {
                "path": relative_path,
                "kind": kind,
                "role": classification.role,
                "adaptation": classification.adaptation,
                "study_dependent": classification.study_dependent,
                "matched_rule_id": classification.matched_rule_id,
                "classification_source": classification.classification_source,
                "source_sha256": _content_hash(original),
                "result_sha256": _content_hash(original),
                "status": "pending",
                "inspection_chunks": 0,
                "requested_edits": 0,
                "applied_edits": 0,
                "rejected_edits": 0,
            }

        async def _adapt_file(
            relative_path: str,
            kind: str,
            classification: ReportPackFile,
        ) -> dict[str, Any]:
            instruction = (
                _adapt_instruction_for(kind, relative_path)
                + "\n\n## Active ReportPack policy\n\n"
                + f"- semantic role: {classification.role}\n"
                + f"- adaptation policy: {classification.adaptation}\n"
                + (
                    "- This file must receive at least one material, targeted "
                    "study adaptation; no_change and DELETE are invalid.\n"
                    if classification.adaptation == "required"
                    else "- A reasoned no_change decision is valid after inspection.\n"
                )
            )
            original = generated_files.get(relative_path, "")
            outcome = _base_outcome(relative_path, kind, classification)
            unit_id = f"adapt:{relative_path}"
            unit_inputs = {
                "instruction_sha256": _content_hash(instruction),
                "role": classification.role,
                "adaptation": classification.adaptation,
                "matched_rule_id": classification.matched_rule_id,
            }
            decision = checkpoint.decide(
                unit_id,
                [relative_path],
                unit_inputs=unit_inputs,
            )
            if decision.action == "skip":
                metadata = (
                    decision.record.get("metadata")
                    if isinstance(decision.record, dict)
                    else None
                )
                previous_outcome = metadata.get("outcome") if isinstance(metadata, dict) else None
                if isinstance(previous_outcome, dict):
                    resumed = dict(previous_outcome)
                    resumed["checkpoint_reused"] = True
                    if resumed.get("status") == "deleted":
                        generated_files.pop(relative_path, None)
                        try:
                            generated_paths.remove(str(base / relative_path))
                        except ValueError:
                            pass
                    return resumed
                # An old/partial checkpoint without evidence is not reusable.
                decision = checkpoint.decide(
                    unit_id + ":recovery",
                    [relative_path],
                    unit_inputs=unit_inputs,
                )
            if decision.action == "preserve":
                outcome["status"] = "preserved_edit"
                outcome["reason"] = decision.reason
                outcome["result_sha256"] = _content_hash(original)
                checkpoint.preserve(
                    unit_id,
                    [relative_path],
                    reason=decision.reason,
                    unit_inputs=unit_inputs,
                    metadata={"outcome": outcome},
                )
                return outcome

            def _finish_adaptation() -> dict[str, Any]:
                if outcome.get("status") in {"failed", "partially_adapted"}:
                    checkpoint.fail(
                        unit_id,
                        [relative_path],
                        error=str(outcome.get("reason") or "adaptation failed"),
                        unit_inputs=unit_inputs,
                    )
                else:
                    checkpoint.complete(
                        unit_id,
                        [relative_path],
                        unit_inputs=unit_inputs,
                        metadata={"outcome": outcome},
                    )
                return outcome

            try:
                edits = await _request_file_edits(
                    instruction,
                    original,
                    system_prompt=system_prompt,
                    plan_json=plan_json,
                    file_descriptions=file_desc,
                    uploaded_file_paths=uploaded_file_paths,
                    target_file=relative_path,
                    generated_context=generated_files,
                    study_manifest_json=study_manifest_json,
                    context_paths=_adaptation_context_paths(kind, relative_path, generated_files),
                )
            except LLMProviderError as exc:
                checkpoint.fail(
                    unit_id,
                    [relative_path],
                    error=str(exc),
                    unit_inputs=unit_inputs,
                )
                raise
            except Exception as exc:
                logger.warning("Adapt request failed for %s: %s", relative_path, exc)
                outcome["status"] = "failed"
                outcome["reason"] = str(exc)
                return _finish_adaptation()
            if isinstance(edits, NoChangeDecision):
                outcome["adaptation"] = edits.adaptation
                outcome["inspection_chunks"] = edits.inspected_chunks
                outcome["inspection_sha256"] = edits.content_sha256
                outcome["decision_reason"] = edits.reason
                outcome["decision_evidence"] = list(edits.evidence)
                if classification.adaptation == "required":
                    outcome["status"] = "failed"
                    outcome["reason"] = (
                        "Report-pack policy requires a material study adaptation; "
                        f"model decided no change: {edits.reason}"
                    )
                else:
                    outcome["status"] = "inspected_no_change"
                    outcome["reason"] = edits.reason
                return _finish_adaptation()
            if isinstance(edits, DeleteDecision):
                outcome["inspection_chunks"] = edits.inspected_chunks
                outcome["inspection_sha256"] = edits.content_sha256
                if classification.adaptation == "required":
                    outcome["status"] = "failed"
                    outcome["reason"] = (
                        "Report-pack policy requires this file; DELETE is not permitted"
                    )
                    return _finish_adaptation()
                if classification.role != "page":
                    outcome["status"] = "failed"
                    outcome["reason"] = (
                        "DELETE is permitted only for report pages; non-page pack "
                        "source must be retained or explicitly marked no_change"
                    )
                    return _finish_adaptation()
                abs_path = base / relative_path
                try:
                    from app.services.edit_engine import EditOperation, EditPolicy, apply_transaction, sha256_bytes

                    current_bytes = abs_path.read_bytes()
                    apply_transaction(
                        base,
                        [EditOperation(path=relative_path, kind="delete", base_sha256=sha256_bytes(current_bytes), reason="ReportPack adaptation delete")],
                        origin="report_pack_adaptation",
                        summary=f"Remove irrelevant source {relative_path}",
                        policy=EditPolicy(allow_create=False, allow_delete=True),
                    )
                except Exception as exc:
                    outcome["status"] = "failed"
                    outcome["reason"] = f"Could not delete file: {exc}"
                    return _finish_adaptation()
                generated_files.pop(relative_path, None)
                try:
                    generated_paths.remove(str(abs_path))
                except ValueError:
                    pass
                outcome["status"] = "deleted"
                outcome["result_sha256"] = None
                _report("report_fill", "running", {"detail": f"Removed irrelevant file {relative_path}"})
                return _finish_adaptation()
            if not isinstance(edits, AdaptationEdits):
                outcome["status"] = "failed"
                outcome["reason"] = "Unknown adaptation decision type"
                return _finish_adaptation()
            outcome["inspection_chunks"] = edits.inspected_chunks
            outcome["inspection_sha256"] = edits.content_sha256
            outcome["adaptation"] = edits.adaptation
            requested_edits = list(edits.edits)
            outcome["requested_edits"] = len(requested_edits)

            updated, applied_count, rejected = _apply_edits_with_report(
                original,
                requested_edits,
                relative_path,
            )
            outcome["applied_edits"] = applied_count
            outcome["rejected_edits"] = len(rejected)
            if rejected:
                outcome["rejection_reasons"] = rejected
            if applied_count == 0:
                outcome["status"] = "failed"
                outcome["reason"] = "No requested edit could be applied"
                return _finish_adaptation()
            if updated != original:
                if (
                    classification.adaptation == "required"
                    and not _is_material_adaptation(original, updated, relative_path)
                ):
                    outcome["status"] = "failed"
                    outcome["reason"] = (
                        "Required adaptation changed only formatting or comments; "
                        "study-bearing code or content must change materially"
                    )
                    return _finish_adaptation()
                logger.info(
                    "Adapt %s: %d edit(s), original=%dB updated=%dB",
                    relative_path,
                    len(requested_edits),
                    len(original),
                    len(updated),
                )
                abs_path = base / relative_path
                try:
                    from app.services.edit_engine import EditOperation, EditPolicy, apply_transaction, sha256_bytes

                    apply_transaction(
                        base,
                        [EditOperation(path=relative_path, kind="rewrite", content=updated, base_sha256=sha256_bytes(original.encode("utf-8")), reason="ReportPack adaptation")],
                        origin="report_pack_adaptation",
                        summary=f"Adapt {relative_path} to the observed study",
                        policy=EditPolicy(require_base_for_rewrite=True),
                        validate=True,
                    )
                except Exception as exc:
                    outcome["status"] = "failed"
                    outcome["reason"] = f"Could not commit adaptation: {exc}"
                    return _finish_adaptation()
                generated_files[relative_path] = updated
                outcome["result_sha256"] = _content_hash(updated)
                outcome["status"] = "partially_adapted" if rejected else "adapted"
                if rejected:
                    outcome["reason"] = "One or more requested edits were rejected"
            else:
                outcome["status"] = "failed"
                outcome["reason"] = (
                    "Requested edits applied without changing the file; use an "
                    "explicit no_change decision when no adaptation is needed"
                )
            return _finish_adaptation()

        copied_outcomes: list[dict[str, Any]] = []
        for relative_path, _, kind, classification in spawn_paths:
            if classification.adaptation != "none":
                continue
            outcome = _base_outcome(relative_path, kind, classification)
            outcome["status"] = "copied"
            outcome["reason"] = "Report-pack policy declares this file study-independent"
            copied_outcomes.append(outcome)

        # Preserve useful context without pretending the pack has a complete
        # dependency graph: source first, then pages, then Quarto assembly.
        # Files within a phase remain parallel.
        inspected_outcomes: list[dict[str, Any]] = []
        for roles in (
            {"data_loader", "helper", "analysis", "validator", "static"},
            {"page"},
            {"assembly", "orchestrator"},
        ):
            phase = [
                item
                for item in adaptable_paths
                if item[3].role in roles
            ]
            inspected_outcomes.extend(
                await _gather_fail_fast(
                    *[
                        _adapt_file(relative_path, kind, classification)
                        for relative_path, _, kind, classification in phase
                    ]
                )
            )
        adaptation_outcomes = copied_outcomes + inspected_outcomes
        pack_metadata = report_pack.metadata()
        pack_metadata["prompt_inputs"] = {
            "system_prompt_sha256": _content_hash(system_prompt),
            "analysis_plan_sha256": _content_hash(plan_json),
            "study_manifest_sha256": _content_hash(study_manifest_json),
            "file_descriptions_sha256": _content_hash(file_desc),
        }
        if pack_snapshot_path:
            pack_metadata["snapshot_path"] = pack_snapshot_path
        adaptation_manifest = _write_adaptation_manifest(
            base,
            adaptation_outcomes,
            report_pack=pack_metadata,
        )
        generated_paths.append(str(adaptation_manifest))
        counts = Counter(str(item["status"]) for item in adaptation_outcomes)
        blocking = [
            item
            for item in adaptation_outcomes
            if item["adaptation"] != "none"
            and item["status"] in {"failed", "partially_adapted"}
        ]
        if blocking:
            failed_paths = ", ".join(str(item["path"]) for item in blocking[:8])
            detail = (
                f"Adaptation failed for {len(blocking)} report-pack source file(s): "
                f"{failed_paths}"
            )
            _report(
                "report_fill",
                "failed",
                {
                    "detail": detail,
                    "path": "adaptation_manifest.json",
                    "status_counts": dict(sorted(counts.items())),
                },
            )
            raise AdaptationGateError(detail)
        _report(
            "report_fill",
            "completed",
            {
                "detail": (
                    f"Adaptation decisions recorded for {len(adaptation_outcomes)} "
                    "copied exemplar file(s)"
                ),
                "path": "adaptation_manifest.json",
                "status_counts": dict(sorted(counts.items())),
            },
        )

    # --- Step 3c: presentation gate (prune shells, repair language) ---
    from app.services.qa_gate import prune_files, run_qa

    def _block_required_qa_removals(
        relative_paths: list[str],
        *,
        source: str,
    ) -> None:
        if not relative_paths:
            return
        project_paths = [_qa_project_relative_path(path) for path in relative_paths]
        for outcome in adaptation_outcomes:
            if outcome.get("path") not in project_paths:
                continue
            outcome.setdefault("adaptation_status", outcome.get("status"))
            outcome["status"] = "qa_removal_blocked"
            outcome["reason"] = (
                f"{source} requested deletion, but ReportPack adaptation:required "
                "files must be retained"
            )
        if adaptation_manifest is not None and pack_metadata is not None:
            _finalize_adaptation_outcomes(base, adaptation_outcomes)
            _write_adaptation_manifest(
                base,
                adaptation_outcomes,
                report_pack=pack_metadata,
            )
        detail = (
            f"{source} attempted to remove required ReportPack source: "
            + ", ".join(project_paths)
        )
        _report(
            "qa_gate",
            "failed",
            {"detail": detail, "errors": project_paths},
        )
        raise GenerationQualityError(detail)

    def _prune_structural_findings(relative_paths: list[str]) -> list[str]:
        prunable, preserved, blocked = _partition_qa_removals(
            relative_paths,
            pack_classifications,
        )
        if preserved:
            _report(
                "qa_gate",
                "running",
                {
                    "detail": (
                        "Preserved ReportPack adaptation:none file(s) despite QA "
                        f"prune findings: {', '.join(preserved)}"
                    )
                },
            )
        _block_required_qa_removals(blocked, source="Structural QA")
        removed = prune_files(project_dir, prunable)
        for relative in removed:
            project_relative = _qa_project_relative_path(relative)
            generated_files.pop(project_relative, None)
            try:
                generated_paths.remove(str(base / project_relative))
            except ValueError:
                pass
            checkpoint.complete(
                f"qa_prune:{project_relative}",
                [project_relative],
                unit_inputs={"source": "structural_qa"},
                metadata={"action": "deleted"},
            )
        return removed

    _report("qa_gate", "running", {"detail": "Checking presentation directives"})
    qa = run_qa(project_dir=project_dir, project_name=plan.project_name)
    if qa.structural:
        removed = _prune_structural_findings(qa.structural)
        _report(
            "qa_gate",
            "running",
            {"detail": f"Pruned {len(removed)} unfilled/empty page(s): {', '.join(removed)}"},
        )
        qa = run_qa(project_dir=project_dir, project_name=plan.project_name)

    if qa.language and settings.qa_repair_rounds > 0:
        _report("qa_gate", "running", {"detail": f"Repairing {len(qa.language)} language finding(s)"})
        async def _repair_language(relative: str) -> tuple[str, str] | None:
            findings = [line for line in qa.language if line.startswith(relative)]
            project_relative = _qa_project_relative_path(relative)
            original = generated_files.get(project_relative, "")
            unit_id = f"qa_language:{project_relative}"
            unit_inputs = {"findings_sha256": canonical_sha256(findings)}
            instruction = (
                "The file `code/{relative}` violates the report writing directives. Fix ONLY these findings "
                "with targeted SEARCH/REPLACE edits, keeping the template structure and scientific content:\n- "
                + "\n- ".join(findings)
                + "\n\nReturn ONLY JSON: [{{\"search\": \"...\", \"replace\": \"...\"}}]"
            ).format(relative=relative)
            try:
                edits = await _request_file_edits(
                    instruction,
                    original,
                    system_prompt=system_prompt,
                    plan_json=plan_json,
                    file_descriptions=file_desc,
                    uploaded_file_paths=uploaded_file_paths,
                    target_file=project_relative,
                    generated_context=generated_files,
                    study_manifest_json=study_manifest_json,
                )
            except LLMProviderError as exc:
                checkpoint.fail(
                    unit_id,
                    [project_relative],
                    error=str(exc),
                    unit_inputs=unit_inputs,
                )
                raise
            except Exception as exc:
                logger.warning("Language repair failed for %s: %s", relative, exc)
                checkpoint.fail(
                    unit_id,
                    [project_relative],
                    error=str(exc),
                    unit_inputs=unit_inputs,
                )
                return None
            if isinstance(edits, DeleteDecision):
                classification = pack_classifications.get(project_relative)
                if classification is not None and classification.adaptation == "none":
                    return "preserved", relative
                if classification is not None and classification.adaptation == "required":
                    return "blocked", relative
                target = base / project_relative
                try:
                    from app.services.edit_engine import EditOperation, EditPolicy, apply_transaction, sha256_bytes

                    if target.is_file():
                        apply_transaction(
                            base,
                            [EditOperation(path=project_relative, kind="delete", base_sha256=sha256_bytes(target.read_bytes()), reason="Remove language QA finding")],
                            origin="qa_language_repair",
                            summary=f"Remove language QA finding {project_relative}",
                            policy=EditPolicy(allow_create=False, allow_delete=True),
                            validate=True,
                        )
                except Exception as exc:
                    checkpoint.fail(
                        unit_id,
                        [project_relative],
                        error=f"Language repair delete failed: {exc}",
                        unit_inputs=unit_inputs,
                    )
                    logger.warning("Language repair delete failed for %s: %s", project_relative, exc)
                    return None
                generated_files.pop(project_relative, None)
                try:
                    generated_paths.remove(str(target))
                except ValueError:
                    pass
                checkpoint.complete(
                    unit_id,
                    [project_relative],
                    unit_inputs=unit_inputs,
                    metadata={"action": "deleted"},
                )
                return "deleted", relative
            if isinstance(edits, NoChangeDecision):
                checkpoint.fail(
                    unit_id,
                    [project_relative],
                    error="Language finding was not repaired",
                    unit_inputs=unit_inputs,
                )
                return None
            if not isinstance(edits, AdaptationEdits):
                checkpoint.fail(
                    unit_id,
                    [project_relative],
                    error="Language repair returned an invalid decision",
                    unit_inputs=unit_inputs,
                )
                return None
            updated = _apply_edits_to_file(
                original,
                list(edits.edits),
                project_relative,
            )
            if updated != original:
                abs_path = base / project_relative
                try:
                    from app.services.edit_engine import (
                        EditOperation,
                        EditPolicy,
                        apply_transaction,
                        sha256_bytes,
                    )

                    apply_transaction(
                        base,
                        [
                            EditOperation(
                                path=project_relative,
                                kind="rewrite",
                                content=updated,
                                base_sha256=sha256_bytes(abs_path.read_bytes()),
                                reason="Presentation language repair",
                            )
                        ],
                        origin="qa_language_repair",
                        summary=f"Repair presentation language in {project_relative}",
                        policy=EditPolicy(require_base_for_rewrite=True),
                        validate=True,
                    )
                except Exception as exc:
                    checkpoint.fail(
                        unit_id,
                        [project_relative],
                        error=f"Language repair commit failed: {exc}",
                        unit_inputs=unit_inputs,
                    )
                    logger.warning("Language repair commit failed for %s: %s", project_relative, exc)
                    return None
                generated_files[project_relative] = updated
                checkpoint.complete(
                    unit_id,
                    [project_relative],
                    unit_inputs=unit_inputs,
                    metadata={"action": "edited"},
                )
            else:
                checkpoint.fail(
                    unit_id,
                    [project_relative],
                    error="Language repair edits did not change the file",
                    unit_inputs=unit_inputs,
                )
            return None

        language_actions = await _gather_fail_fast(
            *[
                _repair_language(relative)
                for relative in sorted({line.split(":")[0] for line in qa.language})
            ]
        )
        blocked_language = [
            relative
            for action in language_actions
            if action is not None
            for status, relative in [action]
            if status == "blocked"
        ]
        _block_required_qa_removals(
            blocked_language,
            source="Language QA repair",
        )
        qa = run_qa(project_dir=project_dir, project_name=plan.project_name)
        if qa.structural:
            _prune_structural_findings(qa.structural)
        qa = run_qa(project_dir=project_dir, project_name=plan.project_name)

    if adaptation_manifest is not None and pack_metadata is not None:
        _finalize_adaptation_outcomes(base, adaptation_outcomes)
        _write_adaptation_manifest(
            base,
            adaptation_outcomes,
            report_pack=pack_metadata,
        )
    if qa.language:
        logger.error(
            "Presentation gate has %d blocking language finding(s): %s",
            len(qa.language),
            qa.language[:5],
        )
    if qa.errors or qa.language:
        findings = [*qa.errors, *qa.language]
        detail = "; ".join(findings[:5])
        _report("qa_gate", "failed", {"detail": detail, "errors": findings})
        raise GenerationQualityError(f"Project QA failed: {detail}")
    _report(
        "qa_gate",
        "completed",
        {
            "detail": "Presentation directives verified",
        },
    )

    def _write_deterministic_output(
        relative_path: str,
        content: str,
        *,
        unit_id: str,
    ) -> str:
        unit_inputs = {"desired_sha256": _content_hash(content)}
        decision = checkpoint.decide(
            unit_id,
            [relative_path],
            unit_inputs=unit_inputs,
        )
        target = base / relative_path
        if decision.action == "run":
            _write_file(target, content)
            checkpoint.complete(
                unit_id,
                [relative_path],
                unit_inputs=unit_inputs,
            )
            return content
        if decision.action == "preserve":
            checkpoint.preserve(
                unit_id,
                [relative_path],
                reason=decision.reason,
                unit_inputs=unit_inputs,
            )
        return target.read_text(errors="replace")

    # --- Step 4: _quarto.yml (template-provided unless the domain has none) ---
    if "code/_quarto.yml" in generated_files:
        _report("quarto_yml", "completed", {"detail": "Using the exemplar site configuration", "path": "code/_quarto.yml"})
    else:
        _report("quarto_yml", "running", {"detail": "Writing deterministic Quarto configuration", "path": "code/_quarto.yml"})
        qmd_pages = sorted(
            [
                path.removeprefix("code/")
                for path in generated_files
                if path.startswith("code/") and path.endswith(".qmd")
            ],
            key=_qmd_sort_key,
        )
        navbar = _build_quarto_navigation(qmd_pages)
        quarto_yml = yaml.safe_dump(
            {
                "project": {
                    "type": "website",
                    "output-dir": "../output",
                    "execute-dir": "project",
                    "render": qmd_pages,
                },
                "website": {
                    "title": plan.project_name,
                    "search": True,
                    "navbar": {"left": navbar},
                },
                "format": {
                    "html": {
                        "theme": "cosmo",
                        "toc": True,
                        "toc-depth": 3,
                        "toc-expand": 1,
                        "code-fold": True,
                        "code-summary": "Show code",
                        "number-sections": True,
                        "page-layout": "full",
                        "lightbox": True,
                        "fig-responsive": True,
                    }
                },
            },
            sort_keys=False,
        )
        quarto_yml = _write_deterministic_output(
            "code/_quarto.yml",
            quarto_yml,
            unit_id="assembly:_quarto.yml",
        )
        generated_files["code/_quarto.yml"] = quarto_yml
        generated_paths.append(str(code_dir / "_quarto.yml"))
        _report("quarto_yml", "completed", {"detail": "Wrote code/_quarto.yml", "path": "code/_quarto.yml"})

    # --- Step 5: main.R (template-provided unless the domain has none) ---
    if "code/main.R" in generated_files:
        _report("main_r", "completed", {"detail": "Using the exemplar render orchestrator", "path": "code/main.R"})
    else:
        _report("main_r", "running", {"detail": "Writing render orchestrator", "path": "code/main.R"})
        main_r = """data_status <- system2("Rscript", "data.R")\nif (data_status != 0) stop("data.R failed")\nrender_status <- system2("quarto", c("render"))\nif (render_status != 0) stop("Quarto render failed")\n"""
        main_r = _write_deterministic_output(
            "code/main.R",
            main_r,
            unit_id="assembly:main.R",
        )
        generated_files["code/main.R"] = main_r
        generated_paths.append(str(code_dir / "main.R"))
        _report("main_r", "completed", {"detail": "Wrote code/main.R", "path": "code/main.R"})

    # Freeze the adapted pack's declared runtime order only after every source
    # mutation and required fallback file has completed. Study inputs and code
    # remain adaptive; this contract prevents the runner from inventing a
    # different workflow such as always executing code/data.R.
    from app.services.execution_contract import write_execution_contract

    try:
        _validate_adapted_source_closure(base, report_pack)
    except GenerationQualityError as exc:
        _report(
            "execution_contract",
            "failed",
            {"detail": str(exc), "errors": [str(exc)]},
        )
        raise
    execution_contract = write_execution_contract(base, report_pack)
    if execution_contract is not None:
        generated_paths.append(str(execution_contract))
        _report(
            "execution_contract",
            "completed",
            {
                "detail": "Recorded the report pack's executable workflow",
                "path": execution_contract.name,
            },
        )

    # Resolve the adaptive plan against the pack's declared capabilities. This
    # is a runtime map, not a frozen analysis: capabilities still point at the
    # adapted files and their validators, while the plan chooses the subset.
    from app.services.capability_contract import (
        CapabilityContractError,
        load_capability_contract,
        validate_capability_bindings,
        write_capability_contract,
    )

    try:
        capability_contract = write_capability_contract(base, report_pack, plan)
        resolved_capabilities = load_capability_contract(base)
        capability_validation = validate_capability_bindings(
            base,
            resolved_capabilities,
            run_r_parse=True,
        )
        if not capability_validation.valid:
            errors = [
                issue.as_dict()
                for issue in capability_validation.issues
                if issue.severity == "error"
            ]
            raise CapabilityContractError(
                "Capability validator preflight failed: " + json.dumps(errors, sort_keys=True)
            )
    except CapabilityContractError as exc:
        _report("capabilities", "failed", {"detail": str(exc), "errors": [str(exc)]})
        raise GenerationQualityError(str(exc)) from exc
    generated_paths.append(str(capability_contract))
    _report(
        "capabilities",
        "completed",
        {
            "detail": "Resolved plan capabilities and validator bindings",
            "path": str(capability_contract.relative_to(base)),
            "validators": [
                path
                for item in resolved_capabilities.selected
                for path in item.capability.validators
            ],
        },
    )

    # --- Step 6: README.md was created with the deterministic scaffold ---
    _report("readme", "completed", {"detail": "Using deterministic project documentation", "path": "README.md"})

    checkpoint.finish()
    return generated_paths


async def _generate_file(
    system_prompt: str,
    plan_json: str,
    file_descriptions: str,
    uploaded_file_paths: dict[str, list[str]],
    target_file: str,
    instruction: str,
    generated_context: dict[str, str],
    study_manifest_json: str = "{}",
    context_paths: set[str] | None = None,
) -> str:
    """Generate a single file using the LLM."""

    context_text = _build_context_text(
        generated_context,
        exclude=f"code/{target_file}",
        include=context_paths,
    )

    path_mapping = _format_data_path_mapping(uploaded_file_paths)

    user_prompt = f"""## Task

Generate the file `{target_file}` for an omics analysis Quarto project.

{instruction}

## Analysis Plan

```json
{plan_json}
```

## Uploaded Data Files

{file_descriptions}

## Validated Study Manifest

```json
{study_manifest_json}
```

## File Paths (relative to code/ directory)

{path_mapping}

## Previously Generated Files

{context_text}

## Output

Return ONLY the file content. No markdown fences, no explanation. Just the raw file content that should be written to `{target_file}`.
"""

    response = await call_llm(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=8000,
    )

    # Strip markdown code fences if present
    content = response.strip()
    if content.startswith("```"):
        first_newline = content.index("\n")
        content = content[first_newline + 1:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

    return content


def title_case_step_id(step_id: str) -> str:
    """Return a readable workflow step name from an identifier."""
    return step_id.replace("_", " ").title()


def _relative_project_path(base: Path, path: Path) -> str:
    """Return a POSIX-style path relative to the generated project root."""
    return path.relative_to(base).as_posix()


def _qmd_sort_key(path: str) -> tuple[int, int, str]:
    section_order = {
        "index.qmd": 0,
        "alpha": 1,
        "beta": 2,
        "ratio": 3,
        "daa": 4,
        "corr": 5,
        "design": 6,
        "data": 7,
        "primary": 8,
        "secondary": 9,
    }
    page_order = {
        "index.qmd": 0,
        "alpha/alpha.qmd": 0,
        "beta/beta.qmd": 0,
        "beta/permanova.qmd": 1,
        "ratio/ratio.qmd": 0,
        "corr/corr.qmd": 0,
        "design/study_overview.qmd": 0,
        "design/analysis_plan.qmd": 1,
        "design/covariate_diagnostics.qmd": 2,
        "data/data_summary.qmd": 1,
        "data/clinical_characteristics.qmd": 0,
        "data/data_quality_assumptions.qmd": 2,
        "primary/metabolite_panel.qmd": 0,
        "primary/longitudinal_models.qmd": 1,
        "primary/primary_results.qmd": 10,
        "primary/figures.qmd": 11,
        "secondary/supplementary_tables.qmd": 0,
        "secondary/secondary_figures.qmd": 1,
        "secondary/exposure_atlas.qmd": 2,
    }
    section = path.split("/", 1)[0]
    return (
        section_order.get(path, section_order.get(section, 10)),
        page_order.get(path, 99),
        path,
    )


def _build_quarto_navigation(qmd_pages: list[str]) -> list[dict[str, Any]]:
    navigation: list[dict[str, Any]] = []
    if "index.qmd" in qmd_pages:
        navigation.append({"text": "Home", "file": "index.qmd"})

    sections = (
        ("alpha", "Alpha diversity"),
        ("beta", "Beta diversity"),
        ("ratio", "B/F ratio"),
        ("daa", "Differential abundance"),
        ("corr", "Correlations"),
        ("design", "Setup & Design"),
        ("primary", "Primary Analysis"),
        ("secondary", "Secondary Analysis"),
        ("data", "Data"),
    )
    for directory, label in sections:
        pages = [path for path in qmd_pages if path.startswith(f"{directory}/")]
        if not pages:
            continue
        navigation.append(
            {
                "text": label,
                "menu": [
                    {"text": _navigation_page_label(path), "file": path}
                    for path in pages
                ],
            }
        )

    uncategorized = [
        path
        for path in qmd_pages
        if path != "index.qmd" and "/" not in path
    ]
    if uncategorized:
        navigation.append(
            {
                "text": "Other",
                "menu": [
                    {"text": _navigation_page_label(path), "file": path}
                    for path in uncategorized
                ],
            }
        )
    return navigation


def _navigation_page_label(path: str) -> str:
    labels = {
        "alpha/alpha.qmd": "Alpha diversity",
        "beta/beta.qmd": "Beta diversity",
        "beta/permanova.qmd": "PERMANOVA",
        "ratio/ratio.qmd": "B/F ratio",
        "corr/corr.qmd": "Correlations",
        "design/study_overview.qmd": "Study overview",
        "design/analysis_plan.qmd": "Analysis plan",
        "design/covariate_diagnostics.qmd": "Covariate diagnostics",
        "data/data_summary.qmd": "Data summary",
        "data/clinical_characteristics.qmd": "Clinical characteristics",
        "data/data_quality_assumptions.qmd": "Data quality and assumptions",
        "primary/alpha_diversity.qmd": "Alpha diversity",
        "primary/beta_diversity.qmd": "Beta diversity",
        "primary/permanova.qmd": "PERMANOVA",
        "primary/differential_abundance_limrots.qmd": "LimROTS differential abundance",
        "primary/metabolite_panel.qmd": "Metabolite panel",
        "primary/longitudinal_models.qmd": "Longitudinal models",
        "primary/primary_results.qmd": "Primary results",
        "primary/figures.qmd": "Figures",
        "secondary/supplementary_tables.qmd": "Supplementary tables",
        "secondary/secondary_figures.qmd": "Secondary figures",
        "secondary/exposure_atlas.qmd": "Exposure atlas",
    }
    if path in labels:
        return labels[path]
    stem = Path(path).stem
    if path.startswith("daa/"):
        stem = re.sub(r"^daa[_-]", "", stem)
    return title_case_step_id(stem)


def _write_project_scaffold(
    base: Path,
    plan: AnalysisPlan,
    uploaded_file_paths: dict[str, list[str]],
    *,
    checkpoint: GenerationCheckpoint | None = None,
) -> list[Path]:
    """Create deterministic starter files so the workspace becomes visible immediately."""
    code_dir = base / "code"
    output_dir = base / "output"
    code_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(exist_ok=True)

    uploaded_rows = "\n".join(
        f"- `{role}`: `{Path(path).name}`"
        for role, paths in sorted(uploaded_file_paths.items())
        for path in paths
    ) or "- No uploaded files were mapped yet."
    workflow_rows = "\n".join(
        f"- {step.name} (`{step.id}`)" for step in plan.workflow if step.enabled
    ) or "- No enabled workflow steps were found."

    readme = f"""# {plan.project_name}\n\nThis project is being generated by OmicsBase. Files appear here as each build step completes.\n\n## Question\n\n{plan.question}\n\n## Uploaded Data\n\n{uploaded_rows}\n\n## Planned Workflow\n\n{workflow_rows}\n\n## Layout\n\n- `data/` contains uploaded source files.\n- `code/design/` documents the study contract and analysis plan.\n- `code/primary/` contains primary analysis pages.\n- `code/secondary/` contains sensitivity and supporting analyses when requested.\n- `code/data/` contains data summaries and quality checks.\n- `output/` contains rendered report files and machine-readable results.\n"""

    index = f"""---\ntitle: \"{plan.project_name}\"\n---\n\n# {plan.project_name}\n\n## Research Question\n\n{plan.question}\n\n## Analysis Workflow\n\n{workflow_rows}\n\nUse the navigation above to move from study design and validated data through the primary analysis results.\n"""
    study_overview = f"""---\ntitle: "Study overview"\n---\n\n## Research question\n\n{plan.question}\n\n## Study contract\n\n- **Domain:** {plan.domain}\n- **Study type:** {plan.study_type}\n- **Grouping variable:** {plan.grouping_variable or "Not configured"}\n\n## Uploaded inputs\n\n{uploaded_rows}\n"""
    analysis_plan = f"""---\ntitle: "Analysis plan"\n---\n\n## Approved workflow\n\n{workflow_rows}\n\n## Reproducibility contract\n\nThe generated report separates source data, executable analysis code, derived results, and rendered pages. Analysis pages record their parameters and session information.\n"""

    readme_path = base / "README.md"
    index_path = code_dir / "index.qmd"
    study_overview_path = code_dir / "design" / "study_overview.qmd"
    analysis_plan_path = code_dir / "design" / "analysis_plan.qmd"
    preview_path = output_dir / "index.html"
    workflow_preview = "".join(
        f"<li><span></span>{escape(step.name)}</li>" for step in plan.workflow if step.enabled
    )
    preview = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="15">
  <title>{escape(plan.project_name)} · OmicsBase</title>
  <style>
    :root {{ color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }}
    body {{ margin: 0; background: #f8fafc; color: #18181b; }}
    main {{ max-width: 920px; margin: 0 auto; padding: 64px 32px; }}
    .eyebrow {{ color: #0f766e; font-size: 12px; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; }}
    h1 {{ margin: 12px 0 8px; font-size: clamp(30px, 5vw, 52px); line-height: 1.05; letter-spacing: -.04em; }}
    .question {{ max-width: 720px; color: #52525b; font-size: 17px; line-height: 1.7; }}
    .status {{ display: inline-flex; align-items: center; gap: 9px; margin-top: 28px; border: 1px solid #d4d4d8; border-radius: 999px; padding: 8px 12px; background: white; font-size: 13px; }}
    .pulse {{ width: 8px; height: 8px; border-radius: 50%; background: #14b8a6; animation: pulse 1.4s infinite; }}
    section {{ margin-top: 48px; border-top: 1px solid #e4e4e7; padding-top: 28px; }}
    h2 {{ font-size: 18px; }}
    ul {{ list-style: none; padding: 0; display: grid; gap: 12px; }}
    li {{ display: flex; align-items: center; gap: 12px; color: #52525b; }}
    li span {{ width: 9px; height: 9px; border: 2px solid #a1a1aa; border-radius: 50%; }}
    @keyframes pulse {{ 50% {{ opacity: .35; transform: scale(.8); }} }}
  </style>
</head>
<body>
  <main>
    <div class="eyebrow">OmicsBase</div>
    <h1>{escape(plan.project_name)}</h1>
    <p class="question">{escape(plan.question)}</p>
    <div class="status"><span class="pulse"></span>Building the validated analysis report</div>
    <section>
      <h2>Planned analysis</h2>
      <ul>{workflow_preview}</ul>
    </section>
  </main>
</body>
</html>
"""

    def _write_scaffold_file(path: Path, content: str) -> None:
        relative = path.resolve().relative_to(base.resolve()).as_posix()
        if checkpoint is None:
            _write_file(path, content)
            return
        unit_id = f"scaffold:{relative}"
        unit_inputs = {"desired_sha256": _content_hash(content)}
        decision = checkpoint.decide(
            unit_id,
            [relative],
            unit_inputs=unit_inputs,
        )
        if decision.action == "run":
            _write_file(path, content)
            checkpoint.complete(
                unit_id,
                [relative],
                unit_inputs=unit_inputs,
            )
        elif decision.action == "preserve":
            checkpoint.preserve(
                unit_id,
                [relative],
                reason=decision.reason,
                unit_inputs=unit_inputs,
            )

    for path, content in (
        (readme_path, readme),
        (index_path, index),
        (study_overview_path, study_overview),
        (analysis_plan_path, analysis_plan),
        (preview_path, preview),
    ):
        _write_scaffold_file(path, content)
    return [
        readme_path,
        index_path,
        study_overview_path,
        analysis_plan_path,
        preview_path,
    ]


def _step_to_qmd_path(step_id: str) -> tuple[str, str]:
    """Map a workflow step ID to a template-layout path."""
    mapping = {
        "import": ("data", "import.qmd"),
        "quality_control": ("data", "quality_control.qmd"),
        "normalization": ("data", "normalization.qmd"),
        "session_info": ("data", "session_info.qmd"),
        "alpha_diversity": ("alpha", "alpha.qmd"),
        "beta_diversity": ("beta", "beta.qmd"),
        "permanova": ("beta", "permanova.qmd"),
        "bf_ratio": ("ratio", "ratio.qmd"),
        "differential_abundance": ("daa", "daa_differential_abundance.qmd"),
        "targeted_species": ("daa", "daa_interest.qmd"),
        "community_composition": ("corr", "corr.qmd"),
        "taxonomy_bars": ("daa", "taxonomy.qmd"),
        "sensitivity_analysis": ("secondary", "sensitivity_analysis.qmd"),
        "linear_feature_scan": ("primary", "metabolite_panel.qmd"),
        "repeated_measures_mixed_model": ("primary", "longitudinal_models.qmd"),
    }
    return mapping.get(step_id, ("primary", f"{step_id}.qmd"))


def _write_file(path: Path, content: str):
    """Write content to a file, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    logger.info("Generated: %s (%d bytes)", path, len(content))


# --- Instruction templates ---

_DATA_R_INSTRUCTION = """Generate `data.R` — the data loading and preprocessing script.

This script should:
1. Load all required R packages
2. Read the uploaded data files from the `../data/` directory
3. Build the appropriate analysis object (phyloseq, TreeSummarizedExperiment, data.frame — whatever is appropriate for the data)
4. Set up grouping variables, factors, and any necessary preprocessing
5. Save the processed object as an RDS file for other scripts to load
6. Print a summary of the loaded data

Follow the style of existing projects: clean, well-organized, no unnecessary comments.
Source funct.R at the top if helper functions are needed.
"""

_FUNCT_R_INSTRUCTION = """Generate `funct.R` — shared helper functions for the analysis.

This script should contain:
1. All library() calls for packages used across the project
2. Comparison group definitions
3. Helper functions for statistical tests, plotting, and table formatting
4. Any project-specific utility functions

Keep functions focused and reusable. No unnecessary comments that restate what the code does.
"""

_QMD_INSTRUCTION = """Adapt the spawned exemplar page into the analysis page for: {step_name}

Step ID: {step_id}
Method: {method_id} {method_name}
Classification: {classification}
{extra}

The page already carries the house structure (front matter, headings, organization) from the exemplar template. Adapt it:
1. Keep the heading structure and page organization; write the analysis the study actually needs in this page's section.
2. Load the preprocessed data object (from data.R output); source funct.R for shared functions.
3. Substantial R code chunks that do real analysis, publication-quality ggplot2 figures, summary tables.
4. Write scientific narrative — like a careful analyst, never AI drivel. Report the study, not the workflow: no "This page...", no "Why this page exists", no meta-commentary about methods/ensembles.
5. Never copy template study names, visits, diets, or cohort details from the exemplar.
6. Keep sections whose source artifacts do not exist yet, stating what artifact is missing.
7. If this page is NOT relevant to this study, respond with exactly:
   DELETE
   and nothing else.
"""

_CONSENSUS_INSTRUCTION = """Write the consensus/comparison section for the contested step: {step_name}

Step ID: {step_id}
Methods compared: {methods}

The page already carries the house structure from the exemplar template. Adapt it:
1. Load results from each method's saved outputs; compare which features are significant across methods.
2. Venn diagram or UpSet plot showing overlap; consensus table (significant in all) and disagreement table (significant in some).
3. Explain the agreement/disagreement in plain scientific language — what a non-computational biologist can read. Report the results, not the workflow: no "This page...", no ensemble meta-commentary.
4. Flag findings that depend entirely on method choice.
5. If this comparison is not relevant to this study, respond with exactly:
   DELETE
   and nothing else.
"""

_QUARTO_YML_INSTRUCTION = """Generate `_quarto.yml` — the Quarto website configuration.

Use the project type: website
Set output-dir to "../output"
Create a navbar with logical groupings of the analysis pages
Enable: toc, code-fold, cosmo theme, number-sections
Set appropriate figure dimensions and DPI

Reference the previously generated QMD files to build the navigation.
Use a clean Quarto website YAML structure with grouped navbar entries for design,
data, primary, and secondary pages when present.
"""

_MAIN_R_INSTRUCTION = """Generate `main.R` — the orchestration script.

This script should:
1. Source data.R to create/load the processed data object
2. Render all QMD files using quarto::quarto_render() or a simple quarto render call
3. Handle any parameterized renders if needed

If the project is simple enough that `quarto render` from the code/ directory
handles everything, main.R can just call quarto::quarto_render().
If parameterized renders are needed (for example alpha diversity across indices),
loop with lapply / purrr::map and pass parameters into quarto::quarto_render().
"""

_README_INSTRUCTION = """Generate `README.md` for the project.

Include:
1. Project title and description
2. Link to rendered report entry point
3. Source layout description
4. Rendering instructions
5. Data files description
6. Notes on the analysis approach
"""
