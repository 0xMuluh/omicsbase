"""Fast-intent path: answer messages without the tool loop.

A deterministic request-state gate handles clear routes first; ambiguous
messages retain the semantic judge as a safe backstop.
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

_CONCEPTUAL_PREFIX = re.compile(
    r"^(?:what(?:'s| is| are)|why (?:do|does|did)|how (?:does|do|did)|"
    r"explain|define|tell me about|what is the difference between)\b"
)
_CONTEXT_REFERENCE = re.compile(
    r"\b(?:my|our|your|this|that|these|those|it|selected|current|actual|"
    r"specific|workspace|project|notebook|file|csv|tsv|qmd|report|result|"
    r"results|job|run|analysis|error|failure|failed|column|group|sample|"
    r"plot|figure|package|cell|variable|output|data)\b"
)
_DEICTIC_REFERENCE = re.compile(
    r"\b(?:this|that|these|those|it|here|above|below|selected|current)\b"
)
_NOTE_OPERATION = re.compile(
    r"^(?:calculate|compute|run|execute|load|import|inspect|read|plot|compare|"
    r"summari[sz]e|analy[sz]e|continue|retry|rerun|resume|use|show|render|repair|fix|build|edit|change|update|set|check|describe|list|search|find|validate|review)\b"
)
_REQUEST_WRAPPER = re.compile(
    r"^(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|please\s+)"
)
_ACTIVE_FAILURE = {"failed", "failure", "error", "cancelled", "canceled"}
_PROCEDURE_REQUEST = re.compile(
    r"^(?:how (?:do|can) i|can you|could you|please)\b.*\b(?:calculate|compute|"
    r"run|execute|load|import|inspect|read|plot|compare|summari[sz]e|"
    r"analy[sz]e|install|render|continue|retry|rerun)\b"
)
_KNOWLEDGE_REQUEST = re.compile(
    r"\b(?:best practices?|guidelines?|which\s+\w+(?:\s+\w+){0,4}\s+method|what method|choose between|how do i choose|"
    r"literature|evidence|recommended)\b"
)

FAST_PATH_SYSTEM = """You are OmicsBase, answering a scientific question directly without inspecting or modifying the user's workspace. Match the depth and structure of the response to the question. Answer narrow factual questions briefly. For broad explanatory questions such as "tell me about X", "explain X", or "how does X work", provide a clear, well-structured explanation using headings, lists, equations, or examples only where they improve understanding. Cover the most relevant aspects of the topic rather than following a fixed template. These may include the definition, mechanism, key concepts, common methods or metrics, interpretation, applications, and important limitations.

When book excerpts have been supplied and are relevant, ground the answer in them and preserve their citations. If the supplied excerpts are not relevant to the question, ignore them rather than forcing them into the answer, and never invent citations. Do not imply that a file, dataset, variable, method, result, or numerical value exists unless it is present in the supplied context. Clearly distinguish general scientific knowledge from conclusions about the user's own data. If a data-specific conclusion requires the user's actual data, explain the general principle first and then ask the user to provide the specific data or values needed (for example sample counts, group labels, or a short table excerpt); if they prefer, state exactly what would need to be inspected in their workspace."""

JUDGE_SYSTEM = """You are a routing classifier for a scientific analysis platform. Classify the user message into exactly one intent: "conceptual", "needs_tools", or "needs_knowledge".

"conceptual": The request can be answered accurately from general scientific or technical knowledge without inspecting the user's workspace and without consulting specialist literature. This includes definitions, explanations of established concepts, general troubleshooting principles, greetings, and casual statements. A question may mention data or analysis while remaining conceptual when it asks about a general effect or principle. For example, "How does log normalization affect my data?" is conceptual when the user wants to understand the general effect — the answering model can ask the user to provide their data if a concrete answer genuinely requires it. Similarly, "Why would an analysis be slow?" is conceptual, whereas "Why is my analysis slow?" refers to the user's specific run and is "needs_tools".

"needs_tools": The request requires reading, executing, inspecting, creating, modifying, or continuing anything in the user's notebook, workspace, files, datasets, code, plots, recipes, packages, reports, or analysis state. Use this intent for requests about the user's specific results, errors, performance, objects, variables, columns, files, plots, or prior notebook work; for commands such as "continue"; and for actions such as importing data, running code, installing packages, or rendering reports. For example, "Why is this analysis slow?" requires tools when it refers to the user's actual analysis, whereas "What commonly makes an analysis slow?" is conceptual.

"needs_knowledge": The request does not require workspace access, but a reliable answer depends on consulting domain literature, formal guidelines, or methodological references. Use this for questions about method selection, current best practices, competing analytical approaches, specialized workflows, evidentiary claims, or recommendations whose answer should be supported by authoritative scientific sources. Do not use this intent merely because literature could add optional detail.

Choose "needs_tools" whenever a correct answer depends on the user's actual workspace state. Otherwise choose "needs_knowledge" only when external methodological evidence is materially necessary; choose "conceptual" for established explanations answerable directly. When genuinely uncertain, prefer "needs_tools" — routing to the tool loop is the safe default.

Reply with ONLY a JSON object in this exact form: {"intent": "conceptual"}."""

VALID_INTENTS = {"conceptual", "needs_tools", "needs_knowledge"}


def deterministic_intent(
    message: str,
    *,
    lens: str,
    explicit_mutation: bool = False,
    selected_resource: str | None = None,
    selected_content_dirty: bool = False,
    active_job_status: str | None = None,
    prior_tool_activity: bool = False,
    pending_question: bool = False,
    notebook_state: bool = False,
) -> str | None:
    """Resolve only routing decisions that are safe from request state.

    ``None`` deliberately means ambiguous: callers should retain the existing
    semantic judge for that case. The deterministic gate handles explicit
    actions, deictic follow-ups tied to live state, and plainly conceptual
    questions without pretending to understand the user's full intent.
    """
    text = " ".join(str(message or "").strip().lower().split())
    if not text:
        return None

    if explicit_mutation or pending_question:
        return "needs_tools"

    if selected_resource and (
        _DEICTIC_REFERENCE.search(text)
        or selected_content_dirty and _CONTEXT_REFERENCE.search(text)
        or len(text.split()) <= 3
    ):
        return "needs_tools"

    if prior_tool_activity and _DEICTIC_REFERENCE.search(text):
        return "needs_tools"

    status = str(active_job_status or "").strip().lower()
    if status in _ACTIVE_FAILURE and (
        _DEICTIC_REFERENCE.search(text)
        or _CONTEXT_REFERENCE.search(text)
        or len(text.split()) <= 4
    ):
        return "needs_tools"

    if _PROCEDURE_REQUEST.match(text):
        return "needs_tools"

    if _KNOWLEDGE_REQUEST.search(text) and not _DEICTIC_REFERENCE.search(text):
        return "needs_knowledge"

    operation_text = _REQUEST_WRAPPER.sub("", text)
    if _NOTE_OPERATION.match(operation_text):
        return "needs_tools"

    if lens == "note" and notebook_state and _DEICTIC_REFERENCE.search(text):
        return "needs_tools"

    if _CONCEPTUAL_PREFIX.match(text) and not _CONTEXT_REFERENCE.search(text):
        return "conceptual"

    return None


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

    Called only for ambiguous messages that pass the minimal gate. Any judge
    failure (provider error, timeouts) defaults to
    ``needs_tools`` — routing to the tool loop, which has its own graceful
    fallback, instead of answering directly without a semantic check.
    """
    if not settings.fast_path_judge_enabled:
        return "needs_tools"
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
            reasoning_effort=str(getattr(settings, "fast_path_judge_reasoning_effort", "") or "") or None,
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

    This is the candidate sanity check; deterministic request-state routing and
    the semantic judge decide what happens after a message passes it. It rejects
    blank messages and absurdly long ones that would blow up the fast-path prompt.
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
    from app.services.providers import fast_model_for

    return fast_model_for(settings.llm_provider.lower())


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
