"""Home / pre-project conversational agent (talk first, project only when needed)."""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

from app.services.llm import call_llm, stream_llm_text

logger = logging.getLogger(__name__)

HOME_SYSTEM = """You are OmicsBase — an AI that builds reproducible downstream omics analysis reports (R + Quarto).

You are chatting on the landing page before any project exists. Answer conversationally; do not simulate an editor:
- Answer the user directly and helpfully in natural language.
- Do NOT invent that a report is being built.
- Only start a study when the user clearly wants analysis work (data, example datasets, research question, Build a report…).

Return exactly one JSON object:

1. Conversational reply (default for questions like capabilities, how it works, greetings, clarification):
{"type":"reply","message":"your answer in plain language; markdown ok"}

2. Start a study workspace (only when intent is clear):
{"type":"start_study","name":"short project title","question":"research question / instruction","message":"one short sentence confirming what you will set up","use_example":null|"phyloseq::GlobalPatterns"}

Rules for start_study:
- Require clear intent to analyze something, import an example dataset, or build a report.
- If they only ask what you can do / help / who you are → always type=reply.
- If they say use GlobalPatterns / an example dataset → start_study with use_example set.
- Keep name ≤ 60 chars; question should be the actionable instruction.
"""


async def generate_project_title(prompt: str) -> str:
    """Generate a clean 2-4 word scientific topic title for a project using few-shot LLM prompt."""
    system_prompt = (
        "You are a scientific topic title generator. Given a user query or research question, "
        "summarize the core scientific topic into a concise 2 to 4 word Title Case topic name.\n"
        "Examples:\n"
        "- 'can you tell me about the differences in kruskal and fisher exact' -> 'Kruskal vs Fisher Exact Test'\n"
        "- 'what downstream analysis can i do on RNA-seq' -> 'Downstream RNA-seq Workflow'\n"
        "- 'what is ecological alpha diversity' -> 'Alpha Diversity'\n"
        "- 'how to run permanova on phyloseq data' -> 'PERMANOVA Analysis'\n"
        "Return ONLY the 2-4 word title. Do not add quotes, explanation, or punctuation."
    )

    raw = await call_llm(
        system_prompt=system_prompt,
        user_prompt=f'Summarize this query into a title: "{prompt[:300]}"',
        max_tokens=20,
    )
    cleaned = raw.strip().strip('"').strip("'").strip(".").strip()
    return cleaned


async def stream_home_chat(
    message: str,
    db: Any | None = None,
    tenant_id: str = "default_tenant",
    user_id: str = "default_user",
    project_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Decide reply vs start_study, persist messages to ProjectMessage, and auto-title via LLM."""
    from app.models.project import Project, ProjectMessage

    text = (message or "").strip()
    if not text:
        yield {"type": "final", "message": "Tell me what you want to analyze, or ask what I can do."}
        return

    project = None
    if db:
        if project_id:
            project = (
                db.query(Project)
                .filter(Project.id == project_id, Project.tenant_id == tenant_id)
                .first()
            )

        if not project:
            project = Project(
                name="New Discussion",
                question=text,
                status="created",
                agent_state="idle",
                tenant_id=tenant_id,
                owner_id=user_id,
            )
            db.add(project)
            db.commit()
            db.refresh(project)

        # Save user message
        user_msg = ProjectMessage(
            project_id=project.id,
            role="user",
            kind="message",
            content=text,
        )
        db.add(user_msg)
        db.commit()

        yield {"type": "init", "project_id": project.id, "name": project.name}

        # Auto-title project on first user message
        if project.name in ("New Discussion", "New Analysis", "New project") or not project.name:
            title = await generate_project_title(text)
            project.name = title
            db.commit()
            yield {"type": "title_update", "project_id": project.id, "name": title}

    yield {"type": "status", "message": "Thinking"}

    final_reply = ""
    try:
        raw = await call_llm(
            system_prompt=HOME_SYSTEM,
            user_prompt=text,
            response_format="json",
            max_tokens=1200,
        )
        decision = _parse(raw)
    except Exception as exc:
        logger.exception("Home chat failed: %s", exc)
        decision = {"type": "reply", "message": ""}
        yield {"type": "status", "message": "Answering"}
        parts: list[str] = []
        async for chunk in stream_llm_text(
            system_prompt=(
                "You are OmicsBase. Answer briefly what you can do for downstream omics "
                "reports. Do not claim a project or report has been created."
            ),
            user_prompt=text,
            max_tokens=800,
        ):
            parts.append(chunk)
            yield {"type": "token", "token": chunk}
        final_reply = "".join(parts).strip() or "I can help build omics Quarto reports once you share data or an example study."
        yield {"type": "final", "message": final_reply}

        if db and project:
            asst_msg = ProjectMessage(
                project_id=project.id,
                role="assistant",
                kind="message",
                content=final_reply,
            )
            db.add(asst_msg)
            db.commit()
        return

    kind = str(decision.get("type") or "reply").strip().lower()
    if kind == "start_study":
        start_name = str(decision.get("name") or "New analysis")[:72]
        if db and project:
            project.name = start_name
            project.question = str(decision.get("question") or text).strip()[:2000]
            db.commit()

        yield {
            "type": "start_study",
            "project_id": project.id if project else None,
            "name": start_name,
            "question": str(decision.get("question") or text).strip()[:2000],
            "message": str(decision.get("message") or "Setting up a study workspace.").strip(),
            "use_example": decision.get("use_example"),
        }

        if db and project:
            asst_msg = ProjectMessage(
                project_id=project.id,
                role="assistant",
                kind="message",
                content=str(decision.get("message") or "Setting up a study workspace."),
            )
            db.add(asst_msg)
            db.commit()
        return

    reply = str(decision.get("message") or "").strip()
    if not reply:
        reply = (
            "I build reproducible microbiome and metabolomics Quarto reports from your data "
            "(or example datasets). Ask me to analyze a study, attach files, or say "
            "“use GlobalPatterns and build a report.”"
        )

    final_reply = reply
    chunk_size = 24
    for index in range(0, len(reply), chunk_size):
        yield {"type": "token", "token": reply[index : index + chunk_size]}
    yield {"type": "final", "message": final_reply}

    if db and project:
        asst_msg = ProjectMessage(
            project_id=project.id,
            role="assistant",
            kind="message",
            content=final_reply,
        )
        db.add(asst_msg)
        db.commit()


def _parse(response: str) -> dict[str, Any]:
    text = response.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline >= 0:
            text = text[first_newline + 1 :]
        if text.endswith("```"):
            text = text[:-3]
    parsed = json.loads(text.strip())
    if not isinstance(parsed, dict):
        raise ValueError("Home agent returned a non-object")
    return parsed

