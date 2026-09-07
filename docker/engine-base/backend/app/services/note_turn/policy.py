"""Small, explicit policy checks for NoteThread turns."""

from __future__ import annotations

import re


_DEMONSTRATION_METHOD = (
    r"(?:test(?:\s+statistic)?|method(?:ology)?|analysis|statistic(?:al)?|"
    r"algorithm|p[-\s]?value|fdr|false\s+discovery\s+rate|diversity|model|"
    r"correlation|permanova|t[-\s]?test|wilcoxon|anova|regression|ordination|"
    r"normalization|differential\s+abundance)"
)
_DEMONSTRATION_REQUEST = re.compile(
    r"(?:"
    rf"\b(?:demonstrat(?:e|ion)|demo|show)\b.{{0,120}}\b(?:example|{_DEMONSTRATION_METHOD})\b"
    rf"|\bwalk\s+(?:me\s+)?through\b.{{0,120}}\b(?:example|{_DEMONSTRATION_METHOD})\b"
    rf"|\blet(?:s|\x27s)\s+do\b.{{0,120}}\b(?:example|{_DEMONSTRATION_METHOD})\b"
    r"|\b(?:do|run)\b.{0,100}\b(?:example|demonstration|demo)\b"
    r")",
    re.IGNORECASE,
)


def is_demonstration_request(message: str) -> bool:
    """Return whether a message explicitly asks for a method demonstration."""
    text = " ".join(str(message or "").strip().lower().split())
    return bool(_DEMONSTRATION_REQUEST.search(text))


__all__ = ["is_demonstration_request"]
