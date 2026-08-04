"""Safe execution for immutable interactive note cells.

Cells of one NoteThread behave like a Jupyter notebook: they share a single
persistent R workspace (``.omicsbase/note-kernel/workspace.RData``) so later
cells see the variables of earlier ones. Each cell still runs in its own
one-shot Rscript process (the existing Docker/no-network runner), and same-
thread executions are serialized with a lock so the workspace never corrupts.
The isolation boundary is the thread/project, not the cell.
"""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import time
from pathlib import Path
from typing import Any, Callable

from app.config import settings
from app.services.runner import SUBPROCESS_TRUNCATION_MARKER, _run_command

logger = logging.getLogger(__name__)

SUPPORTED_NOTE_LANGUAGES = {"r", "rscript"}
NOTE_RUNNER_VERSION = "note-r-isolated-v3"
PARAMETERS_MAX_CHARS = 64_000
PREVIEW_TRUNCATION_MARKER = "\n[preview truncated]"
NOTE_OUTPUT_ROOT = Path("output") / "derived" / "note-executions"
WORKSPACE_RELATIVE_PATH = Path(".omicsbase") / "note-kernel" / "workspace.RData"
WORKSPACE_OBJECTS_RELATIVE_PATH = Path(".omicsbase") / "note-kernel" / "workspace-objects.txt"
WORKSPACE_LOCK_RELATIVE_PATH = Path(".omicsbase") / "note-kernel" / "execution.lock"
CONSOLE_FILE_NAME = ".note_console.txt"
EVENTS_FILE_NAME = ".note_events.jsonl"

CAPTURE_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".webp"}
CAPTURE_TABLE_SUFFIXES = {".csv", ".tsv"}
CAPTURE_HTML_SUFFIXES = {".html", ".htm"}

_R_EXCLUDED_OUTPUT_NAMES = {"cell.R", "parameters.json"}
_R_DEFAULT_ATTACHED = ("base", "stats", "graphics", "grDevices", "utils", "methods", "datasets")


def normalise_note_language(language: str | None) -> str:
    value = (language or "r").strip().lower()
    if value == "rscript":
        return "r"
    return value


def environment_fingerprint(language: str | None) -> str:
    payload = {
        "runner": NOTE_RUNNER_VERSION,
        "language": normalise_note_language(language),
        "docker_image": settings.docker_image,
        "sandbox": bool(settings.use_docker_sandbox),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def input_fingerprint(
    source: str,
    language: str | None,
    parameters: dict[str, Any] | None,
) -> str:
    payload = {
        "source": source,
        "language": normalise_note_language(language),
        "parameters": parameters or {},
    }
    encoded = json.dumps(payload, default=str, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _preview(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return value[:limit] + PREVIEW_TRUNCATION_MARKER, True


def _evaluate_driver(
    *,
    source: str,
    run_dir_rel: str,
    shared_workspace: bool,
    quiet_package_startup: bool,
    capture_plots: bool,
) -> str:
    """Generate the R driver that runs the cell through ``evaluate::evaluate``.

    Structured, interpreter-style capture:
    - console text, warnings, errors, visible values, and plots are collected
      in stream order as a JSONL event log plus a plain console transcript
    - visible data.frames/tibbles become CSV table artifacts
    - plots are replayed from evaluate's recordedplot into PNG files
    - top-level ``print(x)`` calls are rewritten to ``.note_display(x)`` so an
      explicit print of a table becomes a table artifact instead of flattened
      console text (package-internal print calls are untouched)
    - evaluation continues after errors (``stop_on_error = 0``); the workspace
      is still saved in ``finally`` so a failing cell keeps its partial state
    """
    ws_rel = WORKSPACE_RELATIVE_PATH.as_posix()
    objects_rel = WORKSPACE_OBJECTS_RELATIVE_PATH.as_posix()
    defaults = ", ".join(repr(item) for item in _R_DEFAULT_ATTACHED)
    console_rel = (Path(run_dir_rel) / CONSOLE_FILE_NAME).as_posix()
    events_rel = (Path(run_dir_rel) / EVENTS_FILE_NAME).as_posix()
    plots_rel = (Path(run_dir_rel) / "plots").as_posix()
    tables_rel = (Path(run_dir_rel) / "tables").as_posix()
    q = json.dumps

    parts: list[str] = []
    parts.append(
        ".note_t0 <- proc.time()\n"
        ".note_t_load <- 0\n"
        ".note_t_eval <- 0\n"
        ".note_t_save <- 0\n"
    )
    if shared_workspace:
        quiet_require = (
            "suppressPackageStartupMessages(suppressWarnings(require(.note_p, character.only = TRUE)))"
            if quiet_package_startup
            else "require(.note_p, character.only = TRUE)"
        )
        parts.append(
            "\nif (file.exists(" + q(ws_rel) + ")) "
            "tryCatch(load(" + q(ws_rel) + ", envir = .GlobalEnv), "
            "error = function(e) cat('[note] workspace load failed:', conditionMessage(e), '\\n'))\n"
            ".note_attached <- tryCatch(get('.note_attached_packages', envir = .GlobalEnv), "
            "error = function(e) character())\n"
            "for (.note_p in .note_attached) tryCatch(" + quiet_require + ", error = function(e) NULL)\n"
            ".note_t_load <- (proc.time() - .note_t0)[['elapsed']]\n"
        )
    parts.append(
        "\n.note_console <- " + q(console_rel) + "\n"
        ".note_events <- " + q(events_rel) + "\n"
        ".note_plots_dir <- " + q(plots_rel) + "\n"
        ".note_tables_dir <- " + q(tables_rel) + "\n"
        ".note_seq <- 0L\n"
        ".note_plots <- 0L\n"
        ".note_tables <- 0L\n"
        ".note_capture_plots <- " + ("TRUE" if capture_plots else "FALSE") + "\n"
        ".note_quiet <- " + ("TRUE" if quiet_package_startup else "FALSE") + "\n"
        "dir.create(.note_plots_dir, showWarnings = FALSE, recursive = TRUE)\n"
        "dir.create(.note_tables_dir, showWarnings = FALSE, recursive = TRUE)\n"
        "file.create(.note_console)\n"
        "file.create(.note_events)\n"
        ".note_con <- file(.note_console, open = 'a')\n"
        ".note_ev_con <- file(.note_events, open = 'a')\n"
        ".note_log <- function(x) { cat(x, file = .note_con, sep = ''); invisible(NULL) }\n"
        ".note_emit <- function(type, ..., .path = NULL, .rows = NULL, .cols = NULL) {\n"
        "  .note_seq <<- .note_seq + 1L\n"
        "  rec <- c(list(seq = .note_seq, type = type), list(...))\n"
        "  if (!is.null(.path)) rec$path <- .path\n"
        "  if (!is.null(.rows)) rec$rows <- .rows\n"
        "  if (!is.null(.cols)) rec$cols <- .cols\n"
        "  writeLines(jsonlite::toJSON(rec, auto_unbox = TRUE, null = 'null'), .note_ev_con)\n"
        "  invisible(NULL)\n"
        "}\n"
        ".note_display <- function(x, ...) {\n"
        "  if (is.data.frame(x) && ncol(x) > 0L) {\n"
        "    .note_tables <<- .note_tables + 1L\n"
        "    f <- file.path(.note_tables_dir, sprintf('table_%03d.csv', .note_tables))\n"
        "    write.csv(as.data.frame(x), f, row.names = FALSE)\n"
        "    .note_log(sprintf('[table: %d rows x %d cols]\\n', nrow(x), ncol(x)))\n"
        "    .note_emit('table', .path = f, .rows = nrow(x), .cols = ncol(x))\n"
        "    invisible(x)\n"
        "  } else {\n"
        "    base::print(x, ...)\n"
        "  }\n"
        "}\n"
        ".note_source <- tryCatch({\n"
        "  .note_parsed <- parse(text = " + q(source) + ")\n"
        "  paste(vapply(as.list(.note_parsed), function(e) {\n"
        "    if (is.call(e) && identical(e[[1L]], as.name('print')) && length(e) >= 2L) "
        "e[[1L]] <- as.name('.note_display')\n"
        "    paste(deparse(e, width.cutoff = 500L), collapse = '\\n')\n"
        "  }, character(1)), collapse = '\\n')\n"
        "}, error = function(e) e)\n"
        "tryCatch({\n"
        "  if (inherits(.note_source, 'error')) {\n"
        "    .note_log(paste0('Error: ', conditionMessage(.note_source), '\\n'))\n"
        "    .note_emit('error', content = conditionMessage(.note_source))\n"
        "  } else {\n"
        "    evaluate::evaluate(.note_source, envir = .GlobalEnv, stop_on_error = 0L,\n"
        "      output_handler = evaluate::new_output_handler(\n"
        "        text = function(x) { .note_log(x); .note_emit('text', content = x); invisible(NULL) },\n"
        "        message = function(cond) {\n"
        "          if (.note_quiet && inherits(cond, 'packageStartupMessage')) return(invisible(NULL))\n"
        "          .note_log(paste0(conditionMessage(cond), '\\n'))\n"
        "          invisible(NULL)\n"
        "        },\n"
        "        warning = function(cond) {\n"
        "          .note_log(paste0('Warning: ', conditionMessage(cond), '\\n'))\n"
        "          .note_emit('warning', content = conditionMessage(cond))\n"
        "          invisible(NULL)\n"
        "        },\n"
        "        error = function(cond) {\n"
        "          .note_log(paste0('Error: ', conditionMessage(cond), '\\n'))\n"
        "          .note_emit('error', content = conditionMessage(cond))\n"
        "          invisible(NULL)\n"
        "        },\n"
        "        value = function(x, visible) {\n"
        "          if (!visible) return(invisible(NULL))\n"
        "          if (is.data.frame(x) && ncol(x) > 0L) {\n"
        "            .note_display(x)\n"
        "          } else {\n"
        "            rendered <- paste0(capture.output(print(x)), collapse = '\\n')\n"
        "            .note_log(rendered)\n"
        "            .note_log('\\n')\n"
        "            .note_emit('text', content = paste0(rendered, '\\n'))\n"
        "          }\n"
        "          invisible(NULL)\n"
        "        },\n"
        "        graphics = function(recordedplot) {\n"
        "          if (.note_capture_plots) {\n"
        "            .note_plots <<- .note_plots + 1L\n"
        "            f <- file.path(.note_plots_dir, sprintf('plot_%03d.png', .note_plots))\n"
        "            png(f, width = 800, height = 600, res = 110)\n"
        "            tryCatch({ evaluate::replay(recordedplot) }, finally = dev.off())\n"
        "            .note_emit('plot', .path = f)\n"
        "          } else {\n"
        "            pdf(NULL)\n"
        "            tryCatch({ evaluate::replay(recordedplot) }, finally = dev.off())\n"
        "          }\n"
        "          invisible(NULL)\n"
        "        }\n"
        "      )\n"
        "    )\n"
        "  }\n"
        "  .note_t_eval <- (proc.time() - .note_t0)[['elapsed']]\n"
        "}, finally = {\n"
    )
    if shared_workspace:
        parts.append(
            "  tryCatch({\n"
            "    assign('.note_attached_packages', "
            "setdiff(.packages(), c(" + defaults + ")), envir = .GlobalEnv)\n"
            "    save.image(file = " + q(ws_rel) + ")\n"
            "    dir.create(dirname(" + q(objects_rel) + "), showWarnings = FALSE, recursive = TRUE)\n"
            "    writeLines(sort(ls(.GlobalEnv, all.names = TRUE)), " + q(objects_rel) + ")\n"
            "  }, error = function(e) cat('[note] workspace save failed:', conditionMessage(e), '\\n'))\n"
        )
    parts.append(
        ".note_t_save <- (proc.time() - .note_t0)[['elapsed']]\n"
        "tryCatch({\n"
        "  .note_timing <- list(\n"
        "    total_seconds = (proc.time() - .note_t0)[['elapsed']],\n"
        "    load_seconds = .note_t_load,\n"
        "    eval_seconds = .note_t_eval - .note_t_load,\n"
        "    save_seconds = .note_t_save - .note_t_eval)\n"
        "  writeLines(jsonlite::toJSON(.note_timing, auto_unbox = TRUE, digits = 6),\n"
        "    file.path(dirname(.note_console), 'timing.json'))\n"
        "}, error = function(e) NULL)\n"
    )
    parts.append("})\n")
    return "".join(parts)


def _read_run_outputs(run_dir: Path, output: str) -> tuple[str, list[dict[str, Any]]]:
    """Read the console transcript and structured event log from the run dir.

    Falls back to the captured process output when the R side did not write
    its transcript (e.g. the process was killed before the driver started).
    """
    console_path = run_dir / CONSOLE_FILE_NAME
    console_text = output or ""
    try:
        if console_path.is_file():
            console_text = console_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    events: list[dict[str, Any]] = []
    events_path = run_dir / EVENTS_FILE_NAME
    try:
        if events_path.is_file():
            for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except (ValueError, TypeError):
                    continue
    except OSError:
        pass
    return console_text, _cap_events(events)


def _cap_events(events: list[dict[str, Any]], max_events: int = 1000, max_content: int = 10_000) -> list[dict[str, Any]]:
    """Bound the event log stored in result_metadata."""
    capped: list[dict[str, Any]] = []
    for item in events[-max_events:]:
        item = dict(item)
        content = item.get("content")
        if isinstance(content, str) and len(content) > max_content:
            item["content"] = content[:max_content] + "\n[content truncated]"
        capped.append(item)
    return capped


def _artifact_type_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in CAPTURE_IMAGE_SUFFIXES:
        return "image"
    if suffix in CAPTURE_TABLE_SUFFIXES:
        return "table"
    if suffix in CAPTURE_HTML_SUFFIXES:
        return "html"
    return "file"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _discover_output_artifacts(run_dir: Path, relative_dir: Path) -> list[dict[str, Any]]:
    """Register files produced by the executed cell as durable artifact descriptors."""
    max_artifacts = max(1, int(getattr(settings, "note_execution_max_output_artifacts", 25)))
    max_bytes = max(1, int(getattr(settings, "note_execution_max_output_artifact_bytes", 25 * 1024 * 1024)))
    descriptors: list[dict[str, Any]] = []
    files = sorted((path for path in run_dir.rglob("*") if path.is_file()), key=lambda item: item.as_posix())
    for path in files:
        if len(descriptors) >= max_artifacts:
            break
        if path.name in _R_EXCLUDED_OUTPUT_NAMES or path.name.startswith("."):
            continue
        try:
            relative_to_run = path.relative_to(run_dir)
            byte_size = path.stat().st_size
        except (ValueError, OSError):
            continue
        if byte_size <= 0 or byte_size > max_bytes:
            continue
        relative_path = (relative_dir / relative_to_run).as_posix()
        if relative_path.startswith("/") or any(part == ".." for part in relative_path.split("/")):
            continue
        descriptors.append({
            "artifact_type": _artifact_type_for_path(path),
            "relative_path": relative_path,
            "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "byte_size": byte_size,
            "sha256": _sha256_file(path),
        })
    return descriptors


def _discover_root_outputs(project_dir: Path, started_at: float) -> list[dict[str, Any]]:
    """Register table/HTML files the cell wrote directly into the project root.

    R cells run with the project root as their working directory, so a plain
    ``write.csv("table.csv")`` lands there rather than in the run directory.
    Files are matched by suffix and by having been created or overwritten
    during this execution (mtime), which keeps pre-existing files out.
    """
    max_artifacts = max(1, int(getattr(settings, "note_execution_max_output_artifacts", 25)))
    max_bytes = max(1, int(getattr(settings, "note_execution_max_output_artifact_bytes", 25 * 1024 * 1024)))
    root = Path(project_dir).resolve()
    descriptors: list[dict[str, Any]] = []
    candidates: list[Path] = []
    for suffix in (*CAPTURE_TABLE_SUFFIXES, *CAPTURE_HTML_SUFFIXES):
        try:
            candidates.extend(root.glob("*" + suffix))
        except OSError:
            continue
    for path in sorted(candidates, key=lambda item: item.as_posix()):
        if len(descriptors) >= max_artifacts:
            break
        try:
            if path.stat().st_mtime < started_at - 1:
                continue
            byte_size = path.stat().st_size
            relative_path = path.relative_to(root).as_posix()
        except (ValueError, OSError):
            continue
        if byte_size <= 0 or byte_size > max_bytes:
            continue
        if relative_path.startswith("/") or any(part == ".." for part in relative_path.split("/")):
            continue
        descriptors.append({
            "artifact_type": _artifact_type_for_path(path),
            "relative_path": relative_path,
            "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "byte_size": byte_size,
            "sha256": _sha256_file(path),
        })
    return descriptors


def _execution_directory(project_dir: str, execution_id: str) -> tuple[Path, Path]:
    base = Path(project_dir).resolve()
    relative_dir = Path(".omicsbase") / "note-executions" / execution_id
    run_dir = (base / relative_dir).resolve()
    if base not in run_dir.parents:
        raise ValueError("Execution directory escaped the project workspace")
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir, relative_dir


def _acquire_thread_lock(project_dir: str):
    """Serialize executions of one NoteThread so the shared workspace stays consistent.

    Cells of the same thread run one at a time (flock), while different threads
    execute in parallel because each thread has its own lock file.
    """
    import fcntl

    base = Path(project_dir).resolve()
    lock_path = (base / WORKSPACE_LOCK_RELATIVE_PATH).resolve()
    if base not in lock_path.parents:
        raise ValueError("Execution lock escaped the project workspace")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a")
    fcntl.flock(handle, fcntl.LOCK_EX)
    return handle


def _release_thread_lock(handle) -> None:
    import fcntl

    try:
        fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        handle.close()


def _output_artifact_path(project_dir: str, execution_id: str) -> tuple[Path, Path]:
    if Path(execution_id).name != execution_id or execution_id in {"", ".", ".."}:
        raise ValueError("Invalid note execution identifier")
    base = Path(project_dir).resolve()
    relative_path = NOTE_OUTPUT_ROOT / execution_id / "console.log"
    artifact_path = (base / relative_path).resolve()
    if base not in artifact_path.parents:
        raise ValueError("Output artifact escaped the project workspace")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    return artifact_path, relative_path


async def execute_r_cell(
    *,
    project_dir: str,
    execution_id: str,
    source: str,
    language: str | None,
    parameters: dict[str, Any] | None,
    timeout_seconds: int,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[str, dict[str, Any], str | None]:
    """Execute one persisted R revision and return status, metadata, and error."""
    normalised_language = normalise_note_language(language)
    if normalised_language not in SUPPORTED_NOTE_LANGUAGES:
        raise ValueError(f"Unsupported note-cell language: {language or 'r'}")
    if not source.strip():
        raise ValueError("Cannot execute an empty code cell")

    parameters = parameters or {}
    encoded_parameters = json.dumps(
        parameters,
        default=str,
        sort_keys=True,
        indent=2,
    )
    if len(encoded_parameters) > PARAMETERS_MAX_CHARS:
        raise ValueError("Execution parameters exceed the allowed size")

    run_dir, relative_dir = _execution_directory(project_dir, execution_id)
    script_path = run_dir / "cell.R"
    parameters_path = run_dir / "parameters.json"
    shared_workspace = bool(getattr(settings, "note_execution_shared_workspace", True))
    quiet_package_startup = bool(getattr(settings, "note_execution_quiet_package_startup", True))
    capture_plots = bool(getattr(settings, "note_execution_capture_plots", True))
    use_kernel = bool(getattr(settings, "note_kernel_enabled", True)) and shared_workspace
    parameters_path.write_text(encoded_parameters, encoding="utf-8")

    lock_handle = None
    try:
        if shared_workspace:
            lock_handle = _acquire_thread_lock(project_dir)
        relative_script = (relative_dir / "cell.R").as_posix()
        relative_parameters = (relative_dir / "parameters.json").as_posix()
        started_at = time.time()
        if use_kernel:
            script_path.write_text(source, encoding="utf-8")
            from app.services.note_kernel import (
                KernelCancelled,
                KernelDied,
                KernelTimeout,
                ensure_kernel,
                request_cell,
            )

            kernel = ensure_kernel(project_dir)
            try:
                request_cell(
                    kernel,
                    request_id=execution_id,
                    run_dir_rel=relative_dir.as_posix(),
                    source=source,
                    timeout_seconds=timeout_seconds,
                    cancel_check=cancel_check,
                )
                success = True
                output = ""
            except KernelCancelled:
                success = False
                output = "Process cancelled"
            except KernelTimeout:
                success = False
                output = "Process timed out"
            except KernelDied:
                success = False
                output = "The R kernel process died; the cell was not executed"
        else:
            driver = _evaluate_driver(
                source=source,
                run_dir_rel=relative_dir.as_posix(),
                shared_workspace=shared_workspace,
                quiet_package_startup=quiet_package_startup,
                capture_plots=capture_plots,
            )
            script_path.write_text(driver, encoding="utf-8")
            success, output = await _run_command(
                ["Rscript", "--vanilla", relative_script],
                cwd=str(Path(project_dir).resolve()),
                timeout=timeout_seconds,
                cancel_check=cancel_check,
            )
    finally:
        if lock_handle is not None:
            _release_thread_lock(lock_handle)
    output = output or ""
    console_text, events = _read_run_outputs(run_dir, output)
    artifact_path, relative_artifact = _output_artifact_path(project_dir, execution_id)
    output_bytes = console_text.encode("utf-8", errors="replace")
    temporary_path = artifact_path.with_name(f".{artifact_path.name}.tmp")
    temporary_path.write_bytes(output_bytes)
    temporary_path.replace(artifact_path)
    output_artifact = {
        "artifact_type": "console",
        "relative_path": relative_artifact.as_posix(),
        "mime_type": "text/plain",
        "byte_size": len(output_bytes),
        "sha256": hashlib.sha256(output_bytes).hexdigest(),
    }
    preview_limit = max(1, int(getattr(settings, "note_execution_output_preview_chars", 200_000)))
    output_preview, preview_truncated = _preview(console_text, preview_limit)
    runner_truncated = SUBPROCESS_TRUNCATION_MARKER in output
    had_errors = any(item.get("type") == "error" for item in events)
    output_artifacts = _discover_output_artifacts(run_dir, relative_dir)
    root_artifacts = _discover_root_outputs(Path(project_dir).resolve(), started_at)
    timing: dict[str, Any] = {}
    timing_path = run_dir / "timing.json"
    if timing_path.exists():
        try:
            parsed_timing = json.loads(timing_path.read_text(errors="replace"))
            if isinstance(parsed_timing, dict):
                timing = parsed_timing
        except (ValueError, OSError):
            timing = {}
    timing["r_total_seconds"] = max(0.0, time.time() - started_at)
    metadata = {
        "language": normalised_language,
        "script_path": relative_script,
        "parameters_path": relative_parameters,
        "stdout_preview": output_preview,
        "output_chars": len(console_text),
        "output_truncated": bool(preview_truncated or runner_truncated),
        "timeout_seconds": timeout_seconds,
        "runner_version": NOTE_RUNNER_VERSION,
        "timing": timing,
        "events": events,
        "had_errors": had_errors,
        "artifacts": [output_artifact, *output_artifacts, *root_artifacts],
    }

    if "Process cancelled" in output:
        return "cancelled", metadata, None
    if "Process timed out" in output:
        return "timed_out", metadata, "Cell execution exceeded its timeout"
    if not success:
        return "failed", metadata, output_preview or "R cell execution failed"
    if had_errors:
        return "completed_with_errors", metadata, None
    return "completed", metadata, None

