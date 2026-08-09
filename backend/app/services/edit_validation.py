"""Structured, conservative validation for scientific source edits.

Validation is intentionally separate from the edit engine: the engine owns
atomic bytes and hashes, while this module understands Quarto/R/manifest
semantics.  Callers can run it against a prepared virtual transaction before
commit or against committed paths for diagnostics.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    severity: str
    message: str
    path: str | None = None
    line: int | None = None
    check: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "line": self.line,
            "check": self.check,
        }


@dataclass
class ValidationResult:
    issues: list[ValidationIssue] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    def extend(self, other: "ValidationResult") -> None:
        self.issues.extend(other.issues)
        self.checks.extend(other.checks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "checks": list(dict.fromkeys(self.checks)),
            "issues": [issue.as_dict() for issue in self.issues],
        }


_FRONTMATTER = re.compile(r"\A---\s*\n(?P<body>.*?)(?:\n|\r\n)---\s*(?:\n|\Z)", re.DOTALL)
_R_OPEN = re.compile(r"[({\[]")
_R_CLOSE = {')': '(', '}': '{', ']': '['}


def validate_text(path: str, content: bytes | str, *, run_r_parse: bool = False) -> ValidationResult:
    """Validate one source buffer without writing it."""
    result = ValidationResult()
    relative = str(path).replace("\\", "/")
    if isinstance(content, bytes):
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            result.issues.append(ValidationIssue("invalid_utf8", "error", f"File is not valid UTF-8: {exc}", relative, check="utf8"))
            return result
    else:
        text = content
    result.checks.append("utf8")
    if "\x00" in text:
        result.issues.append(ValidationIssue("nul_byte", "error", "Scientific source contains a NUL byte.", relative, check="text_safety"))
        return result
    suffix = Path(relative).suffix.lower()
    if suffix in {".yml", ".yaml"}:
        _validate_yaml(result, relative, text)
    elif suffix == ".json":
        try:
            json.loads(text)
            result.checks.append("json")
        except json.JSONDecodeError as exc:
            result.issues.append(ValidationIssue("invalid_json", "error", str(exc), relative, exc.lineno, "json"))
    elif suffix in {".qmd", ".rmd"}:
        _validate_frontmatter(result, relative, text)
    elif suffix == ".r":
        _validate_r_structure(result, relative, text)
        if run_r_parse and shutil.which("Rscript"):
            _validate_r_parse(result, relative, text)
    return result


def validate_prepared_transaction(prepared: Any, *, run_r_parse: bool = False) -> ValidationResult:
    """Validate the in-memory after-bytes of a PreparedTransaction."""
    result = ValidationResult()
    for item in getattr(prepared, "files", ()):
        if item.after is None:
            continue
        result.extend(validate_text(item.path, item.after, run_r_parse=run_r_parse))
    return result


def validate_project_paths(
    project_root: str | Path,
    paths: Iterable[str],
    *,
    run_r_parse: bool = False,
) -> ValidationResult:
    root = Path(project_root).resolve()
    result = ValidationResult()
    for relative in paths:
        candidate = (root / str(relative)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            result.issues.append(ValidationIssue("unsafe_path", "error", "Validation path escaped the project root.", str(relative), check="path_jail"))
            continue
        if not candidate.is_file():
            result.issues.append(ValidationIssue("missing_file", "error", "Validated file does not exist.", str(relative), check="filesystem"))
            continue
        result.extend(validate_text(str(relative), candidate.read_bytes(), run_r_parse=run_r_parse))
    return result


def _validate_yaml(result: ValidationResult, path: str, text: str) -> None:
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        result.issues.append(ValidationIssue("invalid_yaml", "error", str(exc), path, check="yaml"))
        return
    if value is not None and not isinstance(value, (dict, list)):
        result.issues.append(ValidationIssue("yaml_root_type", "warning", "YAML root is a scalar; confirm this is intentional.", path, check="yaml"))
    result.checks.append("yaml")


def _validate_frontmatter(result: ValidationResult, path: str, text: str) -> None:
    if not text.startswith("---"):
        result.issues.append(ValidationIssue("missing_frontmatter", "warning", "Quarto source has no YAML front matter; confirm this is intentional.", path, check="quarto_frontmatter"))
        return
    match = _FRONTMATTER.match(text)
    if match is None:
        result.issues.append(ValidationIssue("unterminated_frontmatter", "error", "Quarto YAML front matter is not closed with ---.", path, check="quarto_frontmatter"))
        return
    try:
        value = yaml.safe_load(match.group("body")) or {}
    except yaml.YAMLError as exc:
        result.issues.append(ValidationIssue("invalid_frontmatter", "error", str(exc), path, check="quarto_frontmatter"))
        return
    if not isinstance(value, dict):
        result.issues.append(ValidationIssue("frontmatter_type", "error", "Quarto front matter must be a mapping.", path, check="quarto_frontmatter"))
        return
    result.checks.append("quarto_frontmatter")


def _validate_r_structure(result: ValidationResult, path: str, text: str) -> None:
    stack: list[tuple[str, int]] = []
    quote: str | None = None
    escaped = False
    for line_number, line in enumerate(text.splitlines(), 1):
        for char in line:
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
                continue
            if char in {'"', "'", "`"}:
                quote = char
            elif char in "({[":
                stack.append((char, line_number))
            elif char in _R_CLOSE:
                if not stack or stack[-1][0] != _R_CLOSE[char]:
                    result.issues.append(ValidationIssue("unbalanced_r", "error", f"Unexpected {char}.", path, line_number, "r_structure"))
                    return
                stack.pop()
    if quote:
        result.issues.append(ValidationIssue("unterminated_r_string", "error", "R string literal is not closed.", path, check="r_structure"))
    elif stack:
        opener, line_number = stack[-1]
        result.issues.append(ValidationIssue("unbalanced_r", "error", f"Unclosed {opener}.", path, line_number, "r_structure"))
    else:
        result.checks.append("r_structure")


def _validate_r_parse(result: ValidationResult, path: str, text: str) -> None:
    from app.services.runner import run_command_sync

    with tempfile.TemporaryDirectory(prefix="omicsbase-r-validate-") as temp_dir:
        script = Path(temp_dir) / Path(path).name
        script.write_text(text, encoding="utf-8")
        success, output = run_command_sync(
            ["Rscript", "--vanilla", "-e", "parse(file=commandArgs(trailingOnly=TRUE)[[1]])", str(script)],
            cwd=temp_dir,
            timeout=30,
        )
    if success:
        result.checks.append("r_parse")
    else:
        result.issues.append(ValidationIssue("r_parse_failed", "error", (output or "R parser rejected the source")[-2000:], path, check="r_parse"))


__all__ = ["ValidationIssue", "ValidationResult", "validate_prepared_transaction", "validate_project_paths", "validate_text"]
