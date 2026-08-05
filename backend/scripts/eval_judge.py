"""Evaluate the intent-judge routing prompt against a labeled case set.

Usage (from the backend directory):

    python scripts/eval_judge.py
    LLM_FAST_TARGET=groq:llama-3.3-70b-versatile python scripts/eval_judge.py

The script calls the live fast model once per case (max_tokens is the
configured judge budget), prints a per-case verdict table with accuracy by
intent, and exits non-zero when accuracy drops below a floor. It is a
measurement tool, not a pytest — live LLM calls are too flaky for CI.
"""

from __future__ import annotations

import asyncio
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.services.intent_fastpath import JUDGE_SYSTEM, VALID_INTENTS
from app.services.llm import call_llm, resolve_target

# (message, expected intent, note)
CASES: list[tuple[str, str, str]] = [
    ("What is a p-value?", "conceptual", ""),
    ("Tell me about beta diversity", "conceptual", "established explanation"),
    ("How does log normalization affect my data?", "conceptual", "general principle; user can supply data"),
    ("What commonly makes an analysis slow?", "conceptual", "general causes"),
    ("Why would an analysis be slow?", "conceptual", "general causes"),
    ("Why is my analysis slow?", "needs_tools", "their specific run"),
    ("hello", "conceptual", "greeting"),
    ("thanks!", "conceptual", "casual"),
    ("Explain the difference between Bray-Curtis and Jaccard", "conceptual", ""),
    ("What is a permutation test?", "conceptual", ""),
    ("Calculate the mean of 1, 2, and 3", "conceptual", "no workspace needed"),
    ("What's the formula for Shannon entropy?", "conceptual", ""),
    ("Which method should I use to test beta diversity differences?", "needs_knowledge", "method selection"),
    ("What are the best practices for normalizing microbiome counts?", "needs_knowledge", ""),
    ("How do I choose between DESeq2 and limma?", "needs_knowledge", ""),
    ("Which ordination method works best for compositional data?", "needs_knowledge", ""),
    ("Is PERMANOVA appropriate for my study design?", "needs_tools", "ambiguous; possessive 'my' -> their design"),
    ("Is PERMANOVA appropriate for a two-group design?", "needs_knowledge", "no possessive"),
    ("Which group has higher Shannon diversity in my study?", "needs_tools", ""),
    ("Run a PERMANOVA on my samples", "needs_tools", ""),
    ("Continue", "needs_tools", ""),
    ("Why is my analysis taking so long?", "needs_tools", "their specific run"),
    ("What does this CSV contain?", "needs_tools", "file ref"),
    ("Import the GlobalPatterns dataset", "needs_tools", ""),
    ("Install the phyloseq package", "needs_tools", ""),
    ("Calculate the mean of my samples", "needs_tools", "their data"),
    ("Add a caption to the alpha diversity figure", "needs_tools", ""),
    ("Fix the error in my report", "needs_tools", ""),
    ("What did I do in this notebook yesterday?", "needs_tools", "prior work"),
    ("Compare the two groups in my study", "needs_tools", ""),
    ("Analyze my data", "needs_tools", ""),
    ("What's wrong with my last run?", "needs_tools", ""),
]

MIN_ACCURACY = 0.8


async def judge_once(message: str) -> str:
    provider, model = resolve_target("fast")
    raw = await call_llm(
        system_prompt=JUDGE_SYSTEM,
        user_prompt=(message or "").strip()[:500],
        response_format="json",
        max_tokens=int(getattr(settings, "fast_path_judge_max_tokens", 512) or 512),
        model_override=model,
        provider_override=provider,
    )
    match = re.search(r'"intent"\s*:\s*"([^"]+)"', raw or "")
    intent = match.group(1).strip().lower() if match else ""
    if intent not in VALID_INTENTS:
        return f"INVALID:{raw[:40]!r}"
    return intent


async def main() -> int:
    _, model = resolve_target("fast")
    print(f"Judge model: {model} | max_tokens={getattr(settings, 'fast_path_judge_max_tokens', 512)}")
    per_intent: dict[str, Counter] = {intent: Counter() for intent in VALID_INTENTS}
    correct = 0
    print(f"{'OK?':<5}{'expected':<16}{'got':<16}message")
    for message, expected, note in CASES:
        got = await judge_once(message)
        ok = got == expected
        correct += int(ok)
        per_intent[expected][got] += 1
        print(f"{'OK' if ok else 'MISS':<5}{expected:<16}{got:<16}{message[:52]}")
    total = len(CASES)
    accuracy = correct / total
    print(f"\nAccuracy: {correct}/{total} = {accuracy * 100:.0f}%")
    for intent, counts in per_intent.items():
        size = sum(counts.values())
        hit = counts.get(intent, 0)
        print(f"  {intent:<16}{hit}/{size} correct -> {dict(counts)}")
    return 0 if accuracy >= MIN_ACCURACY else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
