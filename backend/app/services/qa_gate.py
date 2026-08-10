"""Deterministic presentation gate for generated projects.

Enforces the presentation directives (writing-style, generated-report
contract, report architecture) mechanically: no zero-byte files, no unfilled
shells, navigation consistent with files, and language that matches the
house voice. Structural violations are pruned; language violations are
reported for the repair round.
"""

from __future__ import annotations

import re
from pathlib import Path

# Content that marks a spawned-but-unfilled shell.
_SHELL_MARKERS = (
    "FILL: develop this section",
    "FILL: study-specific",
    "<!-- Exemplar:",
    "may contain pending sections",
    "spawned report-surface skeleton",
)
_MIN_CONTENT_CHARS = 400

# House-language violations (writing-style.md).
_META_NARRATION = (
    "this page ",
    "this report ",
    "this section ",
    "why this page exists",
    "one method in a contested ensemble",
    "this page is one method",
    "this page loads",
    "this page compares",
    "this page computes",
    "this page identifies",
    "this page evaluates",
    "decision-point registry",
    "contested step",
    "contested ensemble",
    "the ensemble includes",
    "workflow narration",
)
_FILLER_LEXICON = (
    "comprehensive",
    "fascinating",
    "delve",
    "leverage",
    "seamless",
    "moreover",
    "furthermore",
    "valuable insights",
    "in conclusion",
    "it is important to note",
    "it is worth noting",
    "empowers",
    "unleash",
    "game-changing",
)
# Copied exemplar study terms that must never leak into a new project.
_COPIED_STUDY_NAMES = (
    "prenatal",
    "fopp",
    "linderborg",
    "muluh",
    "oat",
    "rice",
    "child serum",
    "week 6",
    "baseline and week",
    "visit 4",
    "visit 5",
    "visit 6",
    "visit 7",
)
# Implementation jargon that should not appear in reader-facing headings.
_JARGON_HEADINGS = (
    "unnamed-chunk",
    "recipe_parameters",
    "study_config",
    "readr::",
    "dplyr::",
    "tidyr::",
    "knitr::kable(",
    "str_replace",
    "clr_pseudocount",
    "recipe_runtime",
)


class QaResult:
    def __init__(self) -> None:
        self.structural: list[str] = []   # prune these files
        self.language: list[str] = []     # repair these findings
        self.errors: list[str] = []       # gate-blocking errors

    @property
    def passed(self) -> bool:
        return not self.structural and not self.language and not self.errors

    def merge(self, other: "QaResult") -> None:
        self.structural.extend(other.structural)
        self.language.extend(other.language)
        self.errors.extend(other.errors)


_SOURCE_LINT_SUFFIXES = {".r", ".rmd", ".qmd"}


def lint_source_files(project_dir: str | Path) -> list[str]:
    """Find known generated-code failure patterns before report rendering."""
    code_dir = Path(project_dir) / "code"
    if not code_dir.is_dir():
        return []
    findings: list[str] = []
    for path in sorted(code_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _SOURCE_LINT_SUFFIXES:
            continue
        try:
            source = path.read_text(errors="replace")
        except OSError:
            continue
        if path.suffix.lower() in {".qmd", ".rmd"}:
            chunks = re.findall(
                r"```(?:\s*\{r\b[^}]*\}|\s*r)\s*\n?(.*?)```",
                source,
                flags=re.DOTALL | re.IGNORECASE,
            )
            source = "\n".join(chunks)
        relative = path.relative_to(code_dir).as_posix()
        findings.extend(_lint_source_text(source, relative))
    return findings


def _call_matches(text: str, function: str) -> list[tuple[int, int, str]]:
    pattern = re.compile(rf"\b(?:[A-Za-z_][\w.]*)?{re.escape(function)}\s*\(")
    matches: list[tuple[int, int, str]] = []
    for match in pattern.finditer(text):
        depth = 1
        quote = ""
        escaped = False
        close = None
        for index in range(match.end(), min(len(text), match.end() + 4000)):
            char = text[index]
            if quote:
                if escaped:
                    escaped = False
                elif char == chr(92):
                    escaped = True
                elif char == quote:
                    quote = ""
                continue
            if char == "\"" or char == chr(39) or char == chr(96):
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    close = index
                    break
        if close is not None:
            matches.append((match.start(), close + 1, text[match.end():close]))
    return matches


def _lint_finding(text: str, relative: str, start: int, rule: str, message: str) -> str:
    line = text.count("\n", 0, start) + 1
    return f"{relative}:{line}: {rule}: {message}"


def _lint_source_text(text: str, relative: str) -> list[str]:
    findings: list[str] = []
    for start, _, body in _call_matches(text, "slice_head") + _call_matches(text, "slice_tail"):
        if re.search(r"\bn\s*=[^,]*?(?:dplyr::)?n\s*\(", body, flags=re.DOTALL):
            findings.append(
                _lint_finding(
                    text,
                    relative,
                    start,
                    "unsafe_dplyr_n",
                    "do not use dplyr::n() as a dynamic slice bound",
                )
            )
    for start, _, body in _call_matches(text, "if_else"):
        if re.search(r"\b(?:names|has_name)\s*\(", body) and re.search(r"\$|\[\[", body):
            findings.append(
                _lint_finding(
                    text,
                    relative,
                    start,
                    "if_else_missing_column",
                    "branch on column existence before calling if_else",
                )
            )
    for start, _, body in _call_matches(text, "bind_cols"):
        selected_sources = re.findall(
            r"\b([A-Za-z_][A-Za-z0-9_.]*)\s*(?:\$|\[\[|\[[^]\n]{0,100},)",
            body,
        )
        if len(set(selected_sources)) >= 2:
            findings.append(
                _lint_finding(
                    text,
                    relative,
                    start,
                    "independent_bind_cols",
                    "prove row identity before binding independently selected tables",
                )
            )
    for start, end, body in _call_matches(text, "unique"):
        suffix = text[end:]
        prefix = text[max(0, start - 500):start]
        guarded = re.search(r"\bif\s*\([^)]*\b(?:length|nrow|NROW)\s*\(", prefix, flags=re.DOTALL)
        if (re.match(r"\s*\[\[\s*1\s*\]\]", suffix) or re.match(r"\s*\[\s*1\s*\]", suffix)) and not guarded:
            findings.append(
                _lint_finding(
                    text,
                    relative,
                    start,
                    "unchecked_unique_index",
                    "guard direct unique(...)[[1]] or unique(...)[1] access for empty input",
                )
            )
    imported = re.search(r"\b(?:read_sav|read_excel|read_xlsx|read_delim|read_csv)\s*\(", text)
    imported = imported or "haven_labelled" in text
    for start, _, body in _call_matches(text, "pivot_longer"):
        prefix = text[max(0, start - 1600):start]
        if imported and not re.search(r"\b(?:values_transform|values_ptypes)\s*=", body) and not re.search(r"\bas\.(?:numeric|double|character)\s*\(", prefix):
            findings.append(
                _lint_finding(
                    text,
                    relative,
                    start,
                    "mixed_pivot_longer",
                    "coerce imported measurement columns to a common type before pivot_longer",
                )
            )
    for start, _, body in _call_matches(text, "as.numeric"):
        if re.search(r"\b(?:haven_labelled|haven|[A-Za-z_]\w*labelled\w*)\b", body):
            findings.append(
                _lint_finding(
                    text,
                    relative,
                    start,
                    "haven_labelled_numeric",
                    "do not unconditionally coerce haven-labelled data to numeric",
                )
            )
    return findings

def run_qa(project_dir: str, project_name: str = "") -> QaResult:
    """Run the presentation gate over a generated project's code/ tree."""
    result = QaResult()
    base = Path(project_dir)
    code_dir = base / "code"
    if not code_dir.exists():
        result.errors.append("code/ directory missing")
        return result

    qmd_files = [path for path in code_dir.rglob("*.qmd")]
    owned_study_terms = _study_terms(project_name)

    for path in qmd_files:
        relative = path.relative_to(code_dir).as_posix()
        try:
            content = path.read_text(errors="replace")
        except OSError:
            continue
        if not content.strip():
            result.structural.append(relative)
            continue
        # The entry page is always kept even when short; every other page
        # must carry real content or be pruned.
        if relative != "index.qmd" and _is_shell(content):
            result.structural.append(relative)
            continue
        findings = _language_findings(content, relative, owned_study_terms)
        result.language.extend(findings)

    result.errors.extend(lint_source_files(base))

    quarto_yml = code_dir / "_quarto.yml"
    if quarto_yml.exists():
        render_list = _render_entries(quarto_yml.read_text(errors="replace"))
        for entry in render_list:
            if not (code_dir / entry).exists():
                result.errors.append(f"_quarto.yml render entry missing file: {entry}")
    return result


def _is_shell(content: str) -> bool:
    body = content.split("---", 2)[-1] if content.startswith("---") else content
    stripped = "".join(line for line in body.splitlines() if not line.strip().startswith("#"))
    if len(stripped.strip()) < _MIN_CONTENT_CHARS:
        return True
    return any(marker in content for marker in ("FILL: develop this section", "may contain pending sections"))


def _language_findings(content: str, relative: str, owned_study_terms: set[str]) -> list[str]:
    findings: list[str] = []
    # HTML comments (exemplar notes) are not reader-facing; ignore them.
    body = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
    lowered = body.lower()
    for phrase in _META_NARRATION:
        if phrase in lowered:
            findings.append(f"{relative}: meta/workflow narration ('{phrase}')")
            break
    for word in _FILLER_LEXICON:
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            findings.append(f"{relative}: filler/marketing language ('{word}')")
            break
    for term in _COPIED_STUDY_NAMES:
        if term in lowered and term not in owned_study_terms:
            findings.append(f"{relative}: copied template study reference ('{term}')")
            break
    for line in body.splitlines():
        if line.startswith("#") and any(jargon in line.lower() for jargon in _JARGON_HEADINGS):
            findings.append(f"{relative}: implementation jargon in heading ('{line.strip()}')")
            break
    return findings


def _study_terms(project_name: str) -> set[str]:
    lowered = (project_name or "").lower()
    return {term for term in _COPIED_STUDY_NAMES if term in lowered}


def _render_entries(quarto_yml: str) -> list[str]:
    entries: list[str] = []
    in_render = False
    for line in quarto_yml.splitlines():
        if re.match(r"^\s*render:\s*$", line):
            in_render = True
            continue
        if in_render:
            match = re.match(r"^\s*-\s*[\"']?([^\"'#]+?)\.qmd[\"']?\s*$", line)
            if match:
                entry = match.group(1).strip() + ".qmd"
                # Quarto accepts inclusion globs and !-prefixed exclusions.
                # They describe sets, not literal files, so existence checks
                # must not report them as missing paths.
                if not entry.startswith("!") and not any(
                    token in entry for token in ("*", "?", "[")
                ):
                    entries.append(entry)
            elif re.match(r"^\s*-\s*!", line) or line.strip() and not line.strip().startswith("-"):
                in_render = False
    return entries


def prune_files(project_dir: str, relative_paths: list[str]) -> list[str]:
    """Delete structural-violation files through the shared edit journal."""
    from app.services.edit_engine import EditOperation, EditPolicy, EditEngineError, apply_transaction, sha256_bytes

    removed: list[str] = []
    base = Path(project_dir).resolve()
    for relative in relative_paths:
        project_relative = (Path("code") / relative).as_posix()
        target = base / project_relative
        try:
            if target.exists() and target.is_file():
                apply_transaction(
                    base,
                    [EditOperation(path=project_relative, kind="delete", base_sha256=sha256_bytes(target.read_bytes()), reason="Remove structural QA shell")],
                    origin="qa_structural_prune",
                    summary=f"Remove structural QA finding {project_relative}",
                    policy=EditPolicy(allow_create=False, allow_delete=True),
                    validate=True,
                )
                removed.append(relative)
        except (OSError, EditEngineError):
            continue
    return removed
