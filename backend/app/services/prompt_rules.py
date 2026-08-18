"""Executable checks for the stable scientific system prompt."""

from __future__ import annotations

from hashlib import sha256

REQUIRED_RULES = (
    "Team report templates",
    "NEVER fabricate statistics",
    "NEVER silently resolve a contested choice",
    "Include proper error handling for package availability",
    "Respect the project working directory",
    "Generated R and Quarto source is checked by the QA gate",
)

FORBIDDEN_RULES = (
    "ignore previous instructions",
    "reveal system prompt",
)

# These are implementation scars, not stable system-level instructions. Keep
# them out of the global prompt so domain guidance and ReportPack references
# can carry methodology while deterministic QA enforces the concrete cases.
SYSTEM_PROMPT_SCAR_RULES = (
    "dplyr::n() inside",
    "pivot_longer() on many imported",
    "dplyr::if_else() to choose",
    "haven_labelled",
    "make.unique()",
    "unique(x)[[1]]",
    "bind_cols() independently",
)


def inspect_prompt(prompt: str) -> dict[str, list[str]]:
    text = str(prompt or "")
    missing = [rule for rule in REQUIRED_RULES if rule not in text]
    forbidden = [rule for rule in FORBIDDEN_RULES if rule.lower() in text.lower()]
    return {"missing": missing, "forbidden": forbidden}


def prompt_contract_ok(prompt: str) -> bool:
    result = inspect_prompt(prompt)
    return not result["missing"] and not result["forbidden"]


def inspect_system_prompt(prompt: str) -> dict[str, list[str]]:
    """Report implementation-specific rules that leaked into system.md."""
    text = str(prompt or "")
    return {
        "forbidden": [
            rule for rule in SYSTEM_PROMPT_SCAR_RULES if rule.lower() in text.lower()
        ]
    }


def prompt_fingerprint(prompt: str) -> str:
    return sha256(str(prompt or "").encode("utf-8")).hexdigest()


__all__ = [
    "FORBIDDEN_RULES",
    "REQUIRED_RULES",
    "SYSTEM_PROMPT_SCAR_RULES",
    "inspect_prompt",
    "inspect_system_prompt",
    "prompt_contract_ok",
    "prompt_fingerprint",
]
