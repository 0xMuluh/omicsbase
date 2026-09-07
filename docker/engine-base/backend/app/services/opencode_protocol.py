"""OpenCode message folding and OmicsBase event normalization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def format_opencode_error(error: Any) -> str:
    """Render an OpenCode provider/runtime error as readable text."""
    if isinstance(error, str):
        return error.strip()
    if not isinstance(error, dict):
        return ""
    data = error.get("data") if isinstance(error.get("data"), dict) else {}
    detail = str(data.get("message") or error.get("message") or "").strip()
    name = str(error.get("name") or "").strip()
    if detail and name and name not in detail:
        return f"{name}: {detail}"
    return detail or name


def assistant_error_from_info(info: dict[str, Any]) -> str | None:
    message = format_opencode_error(info.get("error"))
    return message or None


def _message_info(item: dict[str, Any]) -> dict[str, Any]:
    info = item.get("info")
    return info if isinstance(info, dict) else {}


def collect_assistant_parts(parts: list[Any] | None) -> tuple[str, str]:
    """Return ``(response_text, reasoning_text)`` from an assistant message."""
    response_chunks: list[str] = []
    reasoning_chunks: list[str] = []
    for part in parts or []:
        if not isinstance(part, dict):
            continue
        part_type = str(part.get("type") or "")
        text = str(part.get("text") or "").strip()
        if not text:
            continue
        if part_type == "text":
            response_chunks.append(text)
        elif part_type == "reasoning":
            reasoning_chunks.append(text)
    return "\n\n".join(response_chunks).strip(), "\n\n".join(reasoning_chunks).strip()


@dataclass
class TurnOutcome:
    """The complete outcome folded from every assistant message in a turn."""

    started: bool = False
    response: str = ""
    reasoning: str = ""
    completed_tools: int = 0
    failed_tools: int = 0
    errors: list[str] = field(default_factory=list)
    incomplete: bool = False
    finish: str = ""


def summarize_turn(
    messages: list[dict[str, Any]],
    *,
    prior_message_ids: set[str] | frozenset[str] = frozenset(),
) -> TurnOutcome:
    """Fold every assistant message produced since the prompt into one outcome."""
    outcome = TurnOutcome()
    response_chunks: list[str] = []
    reasoning_chunks: list[str] = []
    for item in messages:
        info = _message_info(item)
        if str(info.get("role") or "") != "assistant":
            continue
        message_id = str(info.get("id") or "")
        if message_id and message_id in prior_message_ids:
            continue
        outcome.started = True
        error = assistant_error_from_info(info)
        if error and error not in outcome.errors:
            outcome.errors.append(error)
        time_info = info.get("time") if isinstance(info.get("time"), dict) else {}
        if not time_info.get("completed"):
            outcome.incomplete = True
        finish = str(info.get("finish") or "").strip()
        if finish:
            outcome.finish = finish
        response, reasoning = collect_assistant_parts(item.get("parts"))
        if response:
            response_chunks.append(response)
        if reasoning:
            reasoning_chunks.append(reasoning)
        for part in item.get("parts") or []:
            if not isinstance(part, dict) or str(part.get("type") or "") != "tool":
                continue
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            status = str(state.get("status") or "")
            if status == "completed":
                outcome.completed_tools += 1
            elif status in {"error", "failed"}:
                outcome.failed_tools += 1
    outcome.response = "\n\n".join(response_chunks).strip()
    outcome.reasoning = "\n\n".join(reasoning_chunks).strip()
    return outcome


def build_final_event(
    outcome: TurnOutcome,
    *,
    turn_ended: bool = True,
    relay_errors: list[str] | tuple[str, ...] = (),
    asked_user: dict[str, Any] | None = None,
    fallback_response: str = "",
    fallback_reasoning: str = "",
) -> dict[str, Any]:
    """Convert a backend outcome into OmicsBase's stable final event."""
    response = outcome.response or fallback_response.strip()
    reasoning = outcome.reasoning or fallback_reasoning.strip()
    errors = [message for message in [*outcome.errors, *relay_errors] if message]
    if asked_user:
        expected_pause_errors = (
            "messageabortederror",
            "questionrejectederror",
            "question rejected",
            "aborted",
        )
        errors = [
            message
            for message in errors
            if not any(marker in message.lower() for marker in expected_pause_errors)
        ]

    if errors:
        detail = "\n\n".join(dict.fromkeys(errors))
        message = f"{response}\n\n{detail}".strip() if response else detail
        ok = False
    elif asked_user and isinstance(asked_user.get("questions"), list) and asked_user["questions"]:
        first = asked_user["questions"][0]
        prompt = str(first.get("prompt") or "").strip() if isinstance(first, dict) else ""
        message = response or str(asked_user.get("message") or prompt or "The agent needs your answer.").strip()
        ok = True
    elif not turn_ended:
        message = "OpenCode stopped responding before the turn finished."
        if response:
            message = f"{response}\n\n{message}"
        ok = False
    elif not outcome.started:
        message = "OpenCode accepted the prompt but never started a reply."
        ok = False
    elif outcome.incomplete:
        message = "OpenCode stopped mid-turn: its last message never completed."
        if response:
            message = f"{response}\n\n{message}"
        ok = False
    elif response:
        message = response
        ok = True
    elif outcome.completed_tools:
        steps = "step" if outcome.completed_tools == 1 else "steps"
        message = f"OpenCode finished {outcome.completed_tools} tool {steps} and ended the turn without a written reply."
        ok = True
    else:
        message = "OpenCode ended the turn without a reply or a completed tool call."
        if outcome.finish:
            message = f"{message} (finish reason: {outcome.finish})"
        ok = False

    event: dict[str, Any] = {"type": "final", "message": message, "ok": ok}
    if outcome.finish:
        event["finish"] = outcome.finish
    if reasoning:
        event["reasoning"] = reasoning
    if asked_user:
        event["awaiting_answer"] = asked_user
    if not ok:
        event["error"] = "\n\n".join(dict.fromkeys(errors)) if errors else message
    return event


def clarification_request_from_question_event(properties: dict[str, Any]) -> dict[str, Any] | None:
    """Convert an OpenCode question event to OmicsBase's API shape."""
    raw_questions = properties.get("questions")
    if not isinstance(raw_questions, list) or not raw_questions:
        return None
    request_id = str(properties.get("id") or properties.get("requestID") or "").strip()
    if not request_id:
        return None
    questions: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_questions, start=1):
        if not isinstance(raw, dict):
            return None
        prompt = str(raw.get("question") or "").strip()
        if not prompt:
            return None
        options: list[str] = []
        for option in raw.get("options") or []:
            if isinstance(option, dict):
                label = str(option.get("label") or "").strip()
                description = str(option.get("description") or "").strip()
                value = f"{label} — {description}" if label and description else label
            else:
                value = str(option).strip()
            if value:
                options.append(value)
        allow_custom = raw.get("custom") is not False
        if not options and not allow_custom:
            return None
        questions.append({
            "id": f"{request_id}:{index}",
            "prompt": prompt,
            "options": options,
            "multiple": bool(raw.get("multiple")),
            "allow_custom": allow_custom,
        })
    if not questions:
        return None
    return {"message": "The agent needs your decision before it can continue.", "questions": questions}

