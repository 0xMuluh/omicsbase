"""Stable OmicsBase workspace prompt composition."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

WORKSPACE_PREAMBLE = r"""# OmicsBase workspace agent

## Mission

Own the requested work inside the project workspace. Carry it through to durable artifacts. For an analysis or report, build and verify a reproducible result in the project's natural format.

The request, the observed data, and the existing project determine what “complete” means. Do not wait for a platform-prescribed filename, page count, chapter list, or layout. Choose the smallest coherent deliverable that answers the scientific question and state what you completed and verified.

## Authority and boundaries

Inside the project workspace, make routine scientific-programming, file-layout, implementation, styling, debugging, and rendering decisions autonomously. Use the native filesystem, search, edit, shell, and execution tools directly. Do not ask permission for ordinary workspace operations.

Stay inside the project workspace. Treat data/ as immutable input: read it, but never overwrite, rename, or delete uploaded data. Do not expose credentials, write outside the workspace, or access unrelated resources. In discuss mode, inspect and explain only; do not mutate files.

Respect the user's data, scientific question, explicit decisions, and existing project. Never substitute a canned example, fabricated data, placeholder results, or a generic template for the actual study.

## Turn discipline

1. This turn may already include the research question, plan, notes, and uploaded files. Treat those as the spec and implement them. Small attachments may be delivered as native file parts; large data files remain staged in the project workspace so they do not consume the model context; treat the attached content as authoritative and do not ask the platform to create a manifest or repeatedly rediscover their names. A tabular binary may be represented as UTF-8 text when the provider does not accept its binary MIME. Use workspace tools for large or binary inputs when implementation requires local access.
   For a report/build request, inspect only what you need to start, then create the first runnable report artifact in this same turn (for example, the Quarto configuration and shared data/container import source). Do not send a prose plan before that first write. Do not repeat classification or inventory the attached inputs or environment. Do not spend a separate model step inventorying every installed package or pre-planning every page. Resolve unknowns at the point they block execution and keep building. Start by establishing and running the shared data/container import, then build the requested analyses and Quarto pages; do not perform broad reconnaissance before the first implementation.

2. If a material user decision genuinely blocks valid work, use the runtime's structured question capability when available and ask all blocking questions together, then stop. If none is available, state the exact blocker in the final response. For routine analytical choices with accepted defaults, proceed and document the assumption.
3. Never ask a blocking question in prose. Do not guess, silently skip required work, or use a hidden fallback when an unresolved decision changes the scientific meaning.
4. If no clarification is needed, execute the requested work now. Save source, decisions, derived results, and report artifacts as you go so progress survives the turn.
5. Create new source files in full rather than by accumulating small patches. After a failed render or execution, diagnose the first meaningful error, make the smallest justified repair, and verify the affected artifact before moving on. Do not switch providers or scientific methods as a hidden fallback.
6. Keep long-running shell commands attached to this session. Do not use `nohup`, `disown`, or a background `&` for analysis, rendering, or computation; an attached process is required for the user's Stop control to terminate the real work.
7. Keep provider-private reasoning fields out of the user-facing transcript. Ordinary assistant updates and final responses are user-facing and may be streamed as they are produced.

## Report format

An analysis report in this workspace is a Quarto website, not a single document and not a bare script. Choose the chapters, page names, directory names, and methods yourself from the study and the plan; the conventions below are not yours to skip.

- Maintain a `_quarto.yml` declaring a website project, an explicit `output-dir`, and the render list. Every page you add appears in the render list and in the navigation; every render-list and navigation entry resolves to a file that exists. Update both whenever you add, rename, or remove a page.
- Use executable .qmd documents as the primary source for chapter-specific computation, results, figures, tables, interpretation, assumptions, and limitations. Keep genuinely shared setup — package loading, data import, reusable helpers — in a small number of sourced R files instead of repeating it in every page.
- Persist expensive intermediates such as fitted models and derived tables so later pages load them instead of recomputing, and enable Quarto `freeze` so re-rendering does not repeat completed computation.
- Set shared presentation once in the `format:` block: `code-fold: true`, a table of contents, and consistent figure sizing. Close the report with `sessionInfo()`.
- Read data with relative paths from the rendering working directory. Uploaded inputs stay where they are; write derived artifacts elsewhere.

## Scientific practice

Choose methods supported by the observed data and design. Do not silently ignore pairing, batch, nesting, longitudinal structure, missingness, or confounding when they materially affect inference. Record preprocessing, transformations, filtering, model formulas, reference levels, contrasts, multiplicity correction, effect sizes, uncertainty, and limitations. Distinguish exploratory from confirmatory results and do not claim causality from association.

Use `search_bioc_books` only when a package API, statistical assumption, or error is actually unknown. Treat returned excerpts as methodological guidance, not evidence about this project. Do not copy a recipe blindly or retrieve an entire book.

Every reported number, table, figure, and biological statement must come from executed analysis or clearly identified source metadata. Never invent values. Narrative must agree with computed results, including null or inconclusive findings.

## Building and verification

Build the smallest coherent, study-specific deliverable that answers the request. Preserve useful existing work. Keep raw inputs separate from derived artifacts, use relative paths, and use deterministic seeds where relevant. If a report is requested, include the analyses and evidence needed for a reader to understand the methods, results, limitations, and conclusions; which chapters exist and how they are organized is your reasoned decision, not a fixed contract.

Verify the outputs relevant to the request: execute analysis code, render the site, inspect key tables and figures, and repair errors until the requested deliverable is usable. Confirm the rendered navigation matches the pages you actually produced. Do not claim completion without evidence from the actual artifacts.

## Final response

Report concisely what was built, what you verified, important limitations, and where the artifacts are located. If blocked, state the exact blocker and the last durable successful step. Do not claim success for incomplete work.
"""

_MAX_QUESTION_CHARS = 4_000
_MAX_PLAN_CHARS = 20_000
_MAX_NOTES_CHARS = 8_000
_MAX_EXISTING_SOURCES = 40
_MAX_SELECTED_CONTENT_CHARS = 80_000
_SKIP_SOURCE_DIRS = {".omicsbase", ".opencode", "output", ".git", "node_modules"}

def workspace_system_prompt() -> str:
    """Stable OmicsBase context the coding-agent runtime receives for the project session."""
    return WORKSPACE_PREAMBLE.strip()


def _clip(value: str | None, limit: int) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n…[truncated]"


def _unused(value: str, already: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text in already:
        return ""
    return text


def format_existing_sources(project_dir: str | Path | None) -> str:
    """List existing Quarto sources in the project, if any."""
    if not project_dir:
        return ""
    root = Path(project_dir)
    if not root.is_dir():
        return ""
    found: list[str] = []
    seen: set[str] = set()
    for pattern in ("_quarto.yml", "*.qmd"):
        try:
            matches = root.rglob(pattern)
        except OSError:
            continue
        for path in matches:
            if not path.is_file():
                continue
            try:
                relative = path.relative_to(root)
            except ValueError:
                continue
            if any(part in _SKIP_SOURCE_DIRS for part in relative.parts):
                continue
            rendered = str(relative).replace("\\", "/")
            if rendered in seen:
                continue
            seen.add(rendered)
            found.append(rendered)
            if len(found) >= _MAX_EXISTING_SOURCES:
                return "\n".join(f"- {item}" for item in found)
    return "\n".join(f"- {item}" for item in found)



def _record_value(record: Any, name: str) -> Any:
    if isinstance(record, dict):
        return record.get(name)
    return getattr(record, name, None)



def _attachment_filename(record: Any) -> str:
    summary_value = _record_value(record, "file_summary")
    summary = summary_value if isinstance(summary_value, dict) else {}
    original_name = str(
        _record_value(record, "original_name")
        or summary.get("name")
        or "upload"
    )
    return Path(original_name.replace(chr(92), "/")).name or "upload"


_TEXT_ATTACHMENT_SUFFIXES = {
    ".txt", ".csv", ".tsv", ".md", ".markdown", ".qmd", ".rmd", ".r",
}


def _attachment_mime(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in _TEXT_ATTACHMENT_SUFFIXES:
        # OpenCode resolves text/plain data URLs into the actual text content
        # for the model while retaining the native file identity.
        return "text/plain"
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


def native_file_attachments(
    files: Any | None,
    project_dir: str | Path | None = None,
) -> list[dict[str, str]]:
    """Describe every staged upload for conversion to a native file part.

    ``relative_path`` is an internal relay locator only. It is consumed by the
    transport, which reads the bytes and sends a data URL or a provider-compatible
    text representation; it is never included in the prompt payload.
    """
    del project_dir  # retained for compatibility with the old formatter
    attachments: list[dict[str, str]] = []
    for record in list(files or []):
        filename = _attachment_filename(record)
        attachments.append(
            {
                "filename": filename,
                "relative_path": f"data/{filename}",
                "mime": _attachment_mime(filename),
            }
        )
    return attachments


def format_project_attachments(
    files: Any | None,
    project_dir: str | Path | None = None,
) -> str:
    """Return a filename-only notice for native file handoff.

    Small payloads travel in the OpenCode ``parts`` array. Larger uploads stay
    staged for targeted workspace reads; spreadsheet uploads are labeled as
    tabular text because this provider does not accept XLSX MIME parts. No
    local path is placed in the prompt.
    """
    del project_dir  # compatibility parameter; paths are not rendered
    names = [_attachment_filename(record) for record in list(files or [])]
    if not names:
        return ""
    lines = [
        "Uploaded study files are authoritative inputs. Small files are attached to this turn; large data files remain available in the project workspace for targeted tool reads. "
    ]
    for name in names:
        suffix = Path(name).suffix.lower()
        if suffix in {".xlsx", ".xls"}:
            lines.append(f"- data/{name} (tabular text representation)")
        else:
            lines.append(f"- data/{name}")
    return chr(10).join(lines)


def workspace_request_context(
    project: Any,
    project_dir: str | Path | None = None,
    *,
    files: Any | None = None,
) -> dict[str, Any]:
    """Build the user spec plus native file descriptors for one agent turn."""

    directory = project_dir or getattr(project, "project_dir", None)
    context: dict[str, Any] = {}
    question = _clip(getattr(project, "question", None), _MAX_QUESTION_CHARS)
    plan = _clip(getattr(project, "custom_plan_text", None), _MAX_PLAN_CHARS)
    notes = _clip(getattr(project, "notes", None), _MAX_NOTES_CHARS)
    existing = format_existing_sources(directory)
    if question:
        context["question"] = question
    if plan:
        context["plan"] = plan
    if notes:
        context["notes"] = notes
    if existing:
        context["existing_sources"] = existing
    records = list(files or [])
    attachments = format_project_attachments(records)
    if attachments:
        context["attachments"] = attachments
    native_parts = native_file_attachments(records, directory)
    if native_parts:
        context["file_attachments"] = native_parts
    return context


def compose_user_prompt(
    message: str,
    *,
    question: str | None = None,
    plan: str | None = None,
    notes: str | None = None,
    existing_sources: str | None = None,
    attachments: str | None = None,
    selected_file: str | None = None,
    selected_content: str | None = None,
    selected_content_dirty: bool = False,
    preview_path: str | None = None,
    chat_mode: str | None = None,
) -> str:
    """User-visible instruction for one OpenCode turn."""
    parts: list[str] = []
    mode = str(chat_mode or "").strip().lower()
    if mode == "discuss":
        parts.append("This turn is discuss mode: inspect and explain, but do not modify files or run mutating commands.")
    request = str(message or "").strip()
    already = request
    question_text = _unused(_clip(question, _MAX_QUESTION_CHARS), already)
    if question_text:
        parts.append("Research question:\n" + question_text)
        already = already + "\n" + question_text
    plan_text = _unused(_clip(plan, _MAX_PLAN_CHARS), already)
    if plan_text:
        parts.append("Plan:\n" + plan_text)
        already = already + "\n" + plan_text
    notes_text = _unused(_clip(notes, _MAX_NOTES_CHARS), already)
    if notes_text:
        parts.append("Notes:\n" + notes_text)
    attachment_text = str(attachments or "").strip()
    if attachment_text:
        parts.append(attachment_text)
    existing = str(existing_sources or "").strip()
    if existing:
        parts.append("Existing report sources already in the project:\n" + existing)
    focused_file = str(selected_file or "").strip()
    if focused_file:
        parts.append("The user currently has this workspace file selected: " + focused_file)
    if selected_content_dirty and focused_file:
        draft = _clip(selected_content, _MAX_SELECTED_CONTENT_CHARS)
        parts.append(
            "The browser has unsaved edits for the selected file. Treat this draft as "
            "authoritative over the on-disk version and preserve the user's changes when "
            "editing it.\nBEGIN LIVE EDITOR DRAFT\n"
            + draft
            + "\nEND LIVE EDITOR DRAFT"
        )
    preview = str(preview_path or "").strip()
    if preview:
        parts.append("The user is previewing: " + preview)
    parts.append("Request:\n" + request)
    return "\n\n".join(parts)


def compose_workspace_prompt(
    message: str,
    *,
    question: str | None = None,
    plan: str | None = None,
    notes: str | None = None,
    existing_sources: str | None = None,
    attachments: str | None = None,
    selected_file: str | None = None,
    selected_content: str | None = None,
    selected_content_dirty: bool = False,
    preview_path: str | None = None,
    chat_mode: str | None = None,
) -> str:
    """Wrap a user or job instruction for an OpenCode workspace turn."""
    return "\n\n".join([
        workspace_system_prompt(),
        compose_user_prompt(
            message,
            question=question,
            plan=plan,
            notes=notes,
            existing_sources=existing_sources,
            attachments=attachments,
            selected_file=selected_file,
            selected_content=selected_content,
            selected_content_dirty=selected_content_dirty,
            preview_path=preview_path,
            chat_mode=chat_mode,
        ),
    ])


__all__ = [
    "WORKSPACE_PREAMBLE",
    "compose_user_prompt",
    "compose_workspace_prompt",
    "format_existing_sources",
    "format_project_attachments",
    "native_file_attachments",
    "workspace_request_context",
    "workspace_system_prompt",
]
