"""Executable checks for the stable scientific system prompt."""

from __future__ import annotations

from hashlib import sha256

REQUIRED_RULES = (
    "The active ReportPack defines",
    "NEVER fabricate statistics",
    "Never assume intermediate data frames have rows",
    "Include proper error handling for package availability",
    "Respect the active pack's working directory",
)

FORBIDDEN_RULES = (
    "ignore previous instructions",
    "reveal system prompt",
)


def inspect_prompt(prompt: str) -> dict[str, list[str]]:
    text = str(prompt or "")
    missing = [rule for rule in REQUIRED_RULES if rule not in text]
    forbidden = [rule for rule in FORBIDDEN_RULES if rule.lower() in text.lower()]
    return {"missing": missing, "forbidden": forbidden}


def prompt_contract_ok(prompt: str) -> bool:
    result = inspect_prompt(prompt)
    return not result["missing"] and not result["forbidden"]


def prompt_fingerprint(prompt: str) -> str:
    return sha256(str(prompt or "").encode("utf-8")).hexdigest()


__all__ = [
    "FORBIDDEN_RULES",
    "REQUIRED_RULES",
    "inspect_prompt",
    "prompt_contract_ok",
    "prompt_fingerprint",
]
