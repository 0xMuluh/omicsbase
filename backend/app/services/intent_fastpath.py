"""Fast-intent path: answer messages without the tool loop.

Routing is fully semantic: the fast-model judge decides between conceptual,
needs_tools, and needs_knowledge for every message. The only pre-checks are
sanity bounds (blank, absurdly long). No keyword or command heuristics.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, AsyncIterator

from app.config import settings
from app.services.llm import call_llm, stream_llm_text

logger = logging.getLogger(__name__)

MAX_FAST_PATH_CHARS = 2000

FAST_PATH_SYSTEM = """You are OmicsBase, answering a scientific question directly without inspecting or modifying the user's workspace. Match the depth and structure of the response to the question. Answer narrow factual questions briefly. For broad explanatory questions such as "tell me about X", "explain X", or "how does X work", provide a clear, well-structured explanation using headings, lists, equations, or examples only where they improve understanding. Cover the most relevant aspects of the topic rather than following a fixed template. These may include the definition, mechanism, key concepts, common methods or metrics, interpretation, applications, and important limitations.

When book excerpts have been supplied and are relevant, ground the answer in them and preserve their citations. If the supplied excerpts are not relevant to the question, ignore them rather than forcing them into the answer, and never invent citations. Do not imply that a file, dataset, variable, method, result, or numerical value exists unless it is present in the supplied context. Clearly distinguish general scientific knowledge from conclusions about the user's own data. If a data-specific conclusion requires the user's actual data, explain the general principle first and then ask the user to provide the specific data or values needed (for example sample counts, group labels, or a short table excerpt); if they prefer, state exactly what would need to be inspected in their workspace."""

JUDGE_SYSTEM = """You are a routing classifier for a scientific analysis platform. Classify the user message into exactly one intent: "conceptual", "needs_tools", or "needs_knowledge".

"conceptual": The request can be answered accurately from general scientific or technical knowledge without inspecting the user's workspace and without consulting specialist literature. This includes definitions, explanations of established concepts, general troubleshooting principles, greetings, and casual statements. A question may mention data or analysis while remaining conceptual when it asks about a general effect or principle. For example, "How does log normalization affect my data?" is conceptual when the user wants to understand the general effect — the answering model can ask the user to provide their data if a concrete answer genuinely requires it. Similarly, "Why would an analysis be slow?" is conceptual, whereas "Why is my analysis slow?" refers to the user's specific run and is "needs_tools".

"needs_tools": The request requires reading, executing, inspecting, creating, modifying, or continuing anything in the user's notebook, workspace, files, datasets, code, plots, recipes, packages, reports, or analysis state. Use this intent for requests about the user's specific results, errors, performance, objects, variables, columns, files, plots, or prior notebook work; for commands such as "continue"; and for actions such as importing data, running code, installing packages, or rendering reports. For example, "Why is this analysis slow?" requires tools when it refers to the user's actual analysis, whereas "What commonly makes an analysis slow?" is conceptual.

"needs_knowledge": The request does not require workspace access, but a reliable answer depends on consulting domain literature, formal guidelines, or methodological references. Use this for questions about method selection, current best practices, competing analytical approaches, specialized workflows, evidentiary claims, or recommendations whose answer should be supported by authoritative scientific sources. Do not use this intent merely because literature could add optional detail.

Choose "needs_tools" whenever a correct answer depends on the user's actual workspace state. Otherwise choose "needs_knowledge" only when external methodological evidence is materially necessary; choose "conceptual" for established explanations answerable directly. When genuinely uncertain, prefer "needs_tools" — routing to the tool loop is the safe default.

Reply with ONLY a JSON object in this exact form: {"intent": "conceptual"}."""

VALID_INTENTS = {"conceptual", "needs_tools", "needs_knowledge"}


def record_routing(
    *,
    lens: str,
    message: str,
    decision: str,
    reason: str,
    intent: str | None = None,
    duration_ms: float | None = None,
) -> None:
    """Log one routing decision for the fast/full path telemetry."""
    logger.info(
        "intent_routing lens=%s decision=%s reason=%s intent=%s duration_ms=%s msg=%r",
        lens,
        decision,
        reason,
        intent or "-",
        f"{duration_ms:.0f}" if duration_ms is not None else "-",
        (message or "")[:120],
    )


async def classify_intent(message: str) -> str:
    """Ask the fast model whether a message is conceptual, needs tools, or
    needs knowledge grounding.

    Called for every message that passes the minimal gate. Because the gate
    is permissive, any judge failure (provider error, timeouts) defaults to
    ``needs_tools`` — routing to the tool loop, which has its own graceful
    fallback, instead of answering directly without a semantic check.
    """
    if not settings.fast_path_judge_enabled:
        return "conceptual"
    from app.services.llm import resolve_target

    provider_override, model_override = resolve_target("fast")
    model = model_override or fast_path_model()
    text = (message or "").strip()[:500]
    started = time.monotonic()
    try:
        response = await call_llm(
            system_prompt=JUDGE_SYSTEM,
            user_prompt=text,
            response_format="json",
            max_tokens=int(getattr(settings, "fast_path_judge_max_tokens", 512) or 512),
            model_override=model,
            provider_override=provider_override,
        )
        match = re.search(r'"intent"\s*:\s*"([^"]+)"', response or "")
        intent = match.group(1).strip().lower() if match else ""
        if intent not in VALID_INTENTS:
            # Unparseable judge output is untrusted: route to the tool loop,
            # the safe default with the permissive gate.
            intent = "needs_tools"
        record_routing(
            lens="judge",
            message=text,
            decision="judged",
            reason="llm",
            intent=intent,
            duration_ms=(time.monotonic() - started) * 1000,
        )
        return intent
    except Exception as exc:
        logger.warning("Intent judge failed, routing to tool loop: %s", exc)
        return "needs_tools"


def is_simple_question(message: str) -> bool:
    """Fast-path candidate sanity check.

    There is no command or keyword filtering: routing is decided entirely
    by the LLM judge. This only rejects blank messages and absurdly long
    ones that would blow up the fast-path prompt.
    """
    if not settings.fast_path_enabled:
        return False
    text = (message or "").strip()
    if not text or len(text) > MAX_FAST_PATH_CHARS:
        return False
    return True


def format_knowledge_seed(matches: list[dict[str, Any]]) -> str | None:
    """Build the grounding excerpts block for a fast-path answer."""
    if not matches:
        return None
    return "\n\n".join(
        f"- {match.get('book_title') or 'Bioconductor book'}: "
        f"{' > '.join(match.get('heading_path') or [match.get('title') or 'excerpt'])}\n"
        f"  {str(match.get('prose') or '')[:1200]}\n"
        f"{('- Code (R):\n```r\n' + str(match.get('code') or '')[:400] + '\n```') if match.get('code') else ''}\n"
        f"  Citation: {match.get('citation') or 'Bioconductor book excerpt'}"
        for match in matches[:5]
    )


def fast_path_model() -> str | None:
    """Resolve the fast model for the configured provider."""
    if settings.fast_path_model:
        return settings.fast_path_model
    provider = settings.llm_provider.lower()
    defaults = {
        "qwen": "qwen-plus",
        "groq": "llama-3.3-70b-versatile",
        "gemini": "gemini-2.0-flash",
        "openrouter": "anthropic/claude-3.5-haiku",
        "anthropic": "claude-haiku-4-5",
        "openai": "gpt-4o-mini",
        "deepseek": "deepseek-chat",
        "grok": "grok-2-latest",
        "xai": "grok-2-latest",
        "ollama": None,
    }
    return defaults.get(provider)


async def stream_simple_answer(
    message: str,
    knowledge_context: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream a direct answer for a simple question as agent events."""
    from app.services.llm import resolve_target

    provider_override, model_override = resolve_target("fast")
    model = model_override or fast_path_model()
    yield {"type": "status", "status": "thinking", "message": "Answering directly", "fast": True}
    user_prompt = message
    if knowledge_context:
        user_prompt = (
            f"{message}\n\nRelevant methodological excerpts you may use to ground the answer:\n"
            f"{knowledge_context}"
        )
    chunks: list[str] = []
    async for chunk in stream_llm_text(
        system_prompt=FAST_PATH_SYSTEM,
        user_prompt=user_prompt,
        max_tokens=int(getattr(settings, "fast_path_max_output_tokens", 4000) or 4000),
        model_override=model,
        provider_override=provider_override,
    ):
        chunks.append(chunk)
        yield {"type": "token", "token": chunk}
    answer = "".join(chunks).strip() or "I could not produce a grounded answer."
    yield {"type": "final", "message": answer, "fast": True}
