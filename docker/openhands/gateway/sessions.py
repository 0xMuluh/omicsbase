"""Session management, project conversation registry, and active thread auto-resolution."""
import os
import json
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Header
from fastapi.responses import JSONResponse
import httpx
import jwt

from gateway.core import (
    AUTH_SECRET,
    AGENT_SERVER_URL,
    PROJECTS_ROOT,
    SESSION_REGISTRY_DIR,
    CONVERSATIONS_DIR,
    logger,
    get_session_api_key,
)
from gateway.auth import TicketRequest, verify_ticket, get_authenticated_user

router = APIRouter(tags=["sessions"])


def _get_project_conversations(user_id: str, project_id: str) -> list[dict]:
    if not user_id or not project_id or not SESSION_REGISTRY_DIR.is_dir():
        return []

    results = []
    for reg_file in SESSION_REGISTRY_DIR.glob("*.json"):
        try:
            data = json.loads(reg_file.read_text())
            if str(data.get("user_id")) == str(user_id) and str(data.get("project_id")) == str(project_id):
                cid = data.get("conversation_id")
                if not cid:
                    continue
                norm_cid = cid.replace("-", "")
                meta_file = CONVERSATIONS_DIR / norm_cid / "meta.json"
                title = None
                if meta_file.is_file():
                    try:
                        m = json.loads(meta_file.read_text())
                        title = m.get("title")
                    except Exception:
                        pass

                events_dir = CONVERSATIONS_DIR / norm_cid / "events"
                event_count = len(list(events_dir.glob("*.json"))) if events_dir.is_dir() else 0
                created_at = data.get("created_at")

                results.append({
                    "conversation_id": cid,
                    "title": title or f"Conversation {cid[:5]}",
                    "created_at": created_at,
                    "event_count": event_count,
                    "agent_engine": data.get("agent_engine", "openhands"),
                })
        except Exception:
            pass

    # Sort: conversations with events first by created_at desc, then others by created_at desc
    results.sort(key=lambda x: (1 if x["event_count"] > 0 else 0, x["created_at"] or 0), reverse=True)
    return results


async def ensure_llm_settings():
    api_key = get_session_api_key()
    headers = {"X-Session-API-Key": api_key}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            # 1. Synchronize Provider Connections
            try:
                conns_resp = await client.get(f"{AGENT_SERVER_URL}/api/llm/provider-connections", headers=headers)
                existing_conns = conns_resp.json() if conns_resp.status_code == 200 else []
                existing_by_prov = {c.get("provider"): c.get("id") for c in existing_conns if isinstance(c, dict)}
            except Exception:
                existing_by_prov = {}

            conns_to_ensure = []
            if os.environ.get("GOOGLE_KEY") or os.environ.get("GEMINI_API_KEY"):
                conns_to_ensure.append({
                    "display_name": "Google Gemini",
                    "provider": "gemini",
                    "api_key": os.environ.get("GOOGLE_KEY") or os.environ.get("GEMINI_API_KEY")
                })
            if os.environ.get("ANTHROPIC_API_KEY"):
                conns_to_ensure.append({
                    "display_name": "Anthropic Claude",
                    "provider": "anthropic",
                    "api_key": os.environ["ANTHROPIC_API_KEY"]
                })
            if os.environ.get("OPENAI_API_KEY"):
                conns_to_ensure.append({
                    "display_name": "OpenAI",
                    "provider": "openai",
                    "api_key": os.environ["OPENAI_API_KEY"]
                })
            if os.environ.get("GROQ_API_KEY"):
                conns_to_ensure.append({
                    "display_name": "Groq",
                    "provider": "groq",
                    "api_key": os.environ["GROQ_API_KEY"]
                })
            if os.environ.get("BAI_API_KEY") or os.environ.get("LLM_API_KEY"):
                conns_to_ensure.append({
                    "display_name": "BAI / GLM",
                    "provider": "custom",
                    "base_url": os.environ.get("BAI_BASE_URL") or os.environ.get("LLM_BASE_URL", "https://api.b.ai/v1"),
                    "api_key": os.environ.get("BAI_API_KEY") or os.environ.get("LLM_API_KEY", "")
                })

            for conn in conns_to_ensure:
                try:
                    prov = conn["provider"]
                    if prov in existing_by_prov:
                        cid = existing_by_prov[prov]
                        await client.patch(f"{AGENT_SERVER_URL}/api/llm/provider-connections/{cid}", headers=headers, json=conn)
                    else:
                        await client.post(f"{AGENT_SERVER_URL}/api/llm/provider-connections", headers=headers, json=conn)
                except Exception:
                    pass

            # 2. Synchronize Named Profiles
            profiles_to_ensure = []

            # Google Gemini
            gkey = os.environ.get("GOOGLE_KEY") or os.environ.get("GEMINI_API_KEY")
            if gkey:
                profiles_to_ensure.extend([
                    ("google-gemini-3.8-flash", {"model": "gemini/gemini-3.8-flash", "api_key": gkey}),
                    ("google-gemini-3.7-flash", {"model": "gemini/gemini-3.7-flash", "api_key": gkey}),
                    ("google-gemini-3.6-flash", {"model": "gemini/gemini-3.6-flash", "api_key": gkey}),
                    ("google-gemini-3.1-pro", {"model": "gemini/gemini-3.1-pro-preview", "api_key": gkey}),
                    ("google-gemini-3.1-flash-lite", {"model": "gemini/gemini-3.1-flash-lite-preview", "api_key": gkey}),
                    ("google-gemini", {"model": "gemini/gemini-3.6-flash", "api_key": gkey}),
                ])

            # OpenAI
            okey = os.environ.get("OPENAI_API_KEY")
            if okey:
                profiles_to_ensure.extend([
                    ("openai-gpt-6-astra", {"model": "openai/gpt-6-astra", "api_key": okey}),
                    ("openai-o3-mini", {"model": "openai/o3-mini", "api_key": okey}),
                    ("openai-o1", {"model": "openai/o1", "api_key": okey}),
                    ("openai-gpt-4o", {"model": "openai/gpt-4o", "api_key": okey}),
                    ("openai-gpt-4o-mini", {"model": "openai/gpt-4o-mini", "api_key": okey}),
                    ("openai-astra", {"model": "openai/gpt-6-astra", "api_key": okey}),
                ])

            # Anthropic
            akey = os.environ.get("ANTHROPIC_API_KEY")
            if akey:
                profiles_to_ensure.extend([
                    ("anthropic-claude-fable-5.1", {"model": "anthropic/claude-fable-5-1", "api_key": akey}),
                    ("anthropic-claude-opus-5", {"model": "anthropic/claude-opus-5", "api_key": akey}),
                    ("anthropic-claude-sonnet-5", {"model": "anthropic/claude-sonnet-5", "api_key": akey}),
                    ("anthropic-claude-sonnet-4", {"model": "anthropic/claude-sonnet-4-20250514", "api_key": akey}),
                    ("anthropic-claude-haiku-4.5", {"model": "anthropic/claude-haiku-4-5", "api_key": akey}),
                    ("anthropic-claude", {"model": "anthropic/claude-fable-5-1", "api_key": akey}),
                ])

            # Groq
            gqkey = os.environ.get("GROQ_API_KEY")
            if gqkey:
                profiles_to_ensure.extend([
                    ("groq-gpt-oss-120b", {"model": "groq/openai/gpt-oss-120b", "api_key": gqkey}),
                    ("groq-compound", {"model": "groq/groq/compound", "api_key": gqkey}),
                    ("groq-compound-mini", {"model": "groq/groq/compound-mini", "api_key": gqkey}),
                    ("groq-qwen-27b", {"model": "groq/qwen/qwen3.8-27b", "api_key": gqkey}),
                    ("groq-gpt-oss-20b", {"model": "groq/openai/gpt-oss-20b", "api_key": gqkey}),
                    ("groq-llama", {"model": "groq/openai/gpt-oss-120b", "api_key": gqkey}),
                ])

            # BAI / GLM
            bkey = os.environ.get("BAI_API_KEY") or os.environ.get("LLM_API_KEY")
            burl = os.environ.get("BAI_BASE_URL") or os.environ.get("LLM_BASE_URL", "https://api.b.ai/v1")
            if bkey:
                profiles_to_ensure.extend([
                    ("bai-glm-5.3-flash", {"model": "openai/glm-5.3-flash", "base_url": burl, "api_key": bkey}),
                    ("bai-glm-5.3", {"model": "openai/glm-5.3", "base_url": burl, "api_key": bkey}),
                    ("bai-glm-5.2", {"model": "openai/glm-5.2", "base_url": burl, "api_key": bkey}),
                    ("bai-glm-5.1", {"model": "openai/glm-5.1", "base_url": burl, "api_key": bkey}),
                    ("bai-qwen3.8-flash", {"model": "openai/qwen3.8-flash", "base_url": burl, "api_key": bkey}),
                    ("bai-glm-flash", {"model": "openai/glm-5.3-flash", "base_url": burl, "api_key": bkey}),
                ])

            for name, llm_conf in profiles_to_ensure:
                try:
                    await client.post(
                        f"{AGENT_SERVER_URL}/api/profiles/{name}",
                        headers=headers,
                        json={"llm": llm_conf, "include_secrets": True}
                    )
                except Exception:
                    pass

            # 3. Synchronize Agent Server Settings
            patch_payload = {
                "agent_settings": {
                    "llm": {
                        "model": os.environ.get("LLM_MODEL", "openai/glm-5.3-flash"),
                        "base_url": os.environ.get("LLM_BASE_URL", "https://api.b.ai/v1"),
                        "api_key": os.environ.get("LLM_API_KEY", "")
                    }
                }
            }
            await client.patch(f"{AGENT_SERVER_URL}/api/settings", headers=headers, json=patch_payload)
        except Exception as e:
            logger.warning(f"Could not initialize agent server LLM settings: {e}")


def build_omicsbase_system_prompt(project_id: str, project_dir: str, agent_engine: str) -> str:
    return f"""
# OmicsBase Workspace Agent

This conversation is initialized for OmicsBase Project '{project_id}' in workspace directory '{project_dir}'.
Active agent engine: {agent_engine}.
You are OmicsBase Workspace Agent, an expert computational biologist and bioinformatician specializing in R, Bioconductor, and Quarto.

<ROLE>
Your primary role is to assist users in bioinformatics data analysis, microbiome/genomics workflows, Quarto website report authoring, and statistical modeling.
* The computational environment is strictly centered on R (4.5+/4.6+) and Bioconductor. All data exploration, statistics, data wrangling, and reporting must be performed using R and Quarto (.qmd), NOT Python.
* If the user asks a question, like "why is X happening", don't try to fix the problem. Just give an answer to the question.
</ROLE>

<ENVIRONMENT_SETUP>
* You are operating in a pre-configured OmicsBase container with R 4.6+, Bioconductor, tidyverse, and Quarto pre-installed.
* DO NOT attempt to install Python packages (e.g., `pip install pandas`, `pip install scipy`, `pip install statsmodels`, `pip install matplotlib`, `pip install seaborn`). Python is NOT the analysis language for this workspace.
* All data inspection, metadata loading (e.g., `.xlsx`, `.csv`, `.tsv`), and exploratory analysis must be performed using R (e.g., `Rscript -e "library(readxl); df <- read_excel('...')"` or standard R scripts).
* If an R package is missing, first verify if it is already installed in the R library. If truly needed, install via `Rscript -e "BiocManager::install('package_name')"` or `Rscript -e "install.packages('package_name')"`.
* Never run pip commands or initiate long Python package build loops.
</ENVIRONMENT_SETUP>

<REPORT_FORMAT>
## Report format
Deliver the analysis report as a multi-page Quarto website. Determine the chapters, structure, and analytical methods directly from the study design and research plan.
- Maintain a `_quarto.yml` declaring a `project: type: website`, `output-dir: _site`, and the render list including `index.qmd`. The OmicsBase preview service expects `_site/index.html`. Ensure all reader-facing pages appear in both the render list and site navigation, resolving to existing files.
- Use executable `.qmd` documents for chapter-specific analysis, results, figures, tables, and interpretation. Centralize shared setup (data import, shared models, helper functions) in sourced R scripts.
- All computational code chunks in `.qmd` documents must use the R engine (e.g., ```{{r}}). Do NOT use python code chunks.
- Configure shared document styling in the `format: html:` block: `code-fold: true`, table of contents (`toc: true`), and consistent figure dimensions. Include `sessionInfo()` at the conclusion of the report.
- Use relative paths from the project root. Treat `data/` as read-only input and write all generated assets to dedicated output directories.
- Use HTML, CSS/SCSS, and presentation JavaScript when needed for the website interface; all scientific computation and data analysis must remain in R.
- Establish consistent typography, spacing, colors, figure styling, and readable tables across pages. Preserve the established visual identity during subsequent edits. Lead the overview with the study question, supported findings, and analysis status; connect results to their question, evidence, interpretation, and limitations.
</REPORT_FORMAT>

<WEBSITE_COMPLETION_LOOP>
1. INSPECT: Identify the active project, study plan, available data, existing pages, and preview/render tools. Use the actual project identifier rather than silently falling back to `default`.
2. BUILD: Implement the requested analysis and pages. For a new website, produce a coherent first render early, with honest pending-analysis states where needed, then continue through the requested scope. An early preview is a progress milestone, not the final deliverable.
3. RENDER: Run `quarto render` from the project's root. Inspect the exit status and logs. Fix execution and rendering failures and render again. An old `_site/index.html` is not evidence that the current source rendered successfully.
4. INSPECT OUTPUT: Confirm the current render produced `_site/index.html`, requested pages, figures, and tables. Check navigation and local asset links.
5. VALIDATE SCIENCE: Verify displayed counts and findings against executed R outputs, review meaningful warnings, and ensure pending or failed analyses are not described as completed. Do not silently reuse stale outputs after changing data or analysis code.
6. DELIVER: Finish when the requested scope is complete and relevant checks pass. Provide the verified preview URL when available, a concise description of the result, and material limitations. Never invent a URL or claim checks you did not perform. If a specific blocker prevents completion, clearly mark the result as partial and explain what is needed to resume.
</WEBSITE_COMPLETION_LOOP>

<TROUBLESHOOTING>
* Inspect actual errors, inputs, logs, and environment. Identify likely causes, run targeted checks, apply an evidence-supported fix, and verify again. Summarize the evidence and outcome briefly.
* Adapt your implementation plan autonomously for technical obstacles. Seek clarification only when the solution changes the scientific question, requires missing essential information, or exceeds the authorized scope.
* Never hide errors by silently dropping samples, changing statistical methods, suppressing meaningful warnings, or substituting fabricated results. When repeated attempts fail, change approach based on evidence rather than retrying unchanged commands.
</TROUBLESHOOTING>
""".strip()


@router.post("/api/omicsbase/session")
async def session(
    data: Optional[TicketRequest] = None,
    authorization: Optional[str] = Header(None),
):
    ticket = (data.ticket if data and data.ticket else None) or (authorization.replace("Bearer ", "") if authorization else None)
    if not ticket:
        raise HTTPException(status_code=401, detail="Ticket required")
    claims = verify_ticket(ticket)

    # Ensure Agent Server has LLM credentials configured
    await ensure_llm_settings()

    user_id = str(claims.get("sub"))
    project_id = str(claims.get("project_id"))

    project_convos = _get_project_conversations(user_id, project_id)

    convo_id = data.conversation_id if data and data.conversation_id else None
    convo_valid = True
    if convo_id:
        api_key = get_session_api_key()
        headers = {"X-Session-API-Key": api_key}
        async with httpx.AsyncClient(timeout=5.0) as client:
            try:
                chk = await client.get(f"{AGENT_SERVER_URL}/api/conversations/{convo_id}", headers=headers)
                if chk.status_code == 404:
                    convo_valid = False
            except Exception:
                convo_valid = False

    current_convo_events = 0
    if convo_id:
        for c in project_convos:
            if c.get("conversation_id") == convo_id:
                current_convo_events = c.get("event_count", 0)
                break

    # Auto-resolve to most active/recent session if missing, invalid, or empty while active conversations exist
    if not convo_id or not convo_valid or (current_convo_events == 0 and project_convos and project_convos[0].get("event_count", 0) > 0):
        if project_convos:
            best_convo = project_convos[0]
            convo_id = best_convo["conversation_id"]
            convo_valid = True
        else:
            convo_id = None
            convo_valid = False

    resp = JSONResponse(content={
        "status": "ok",
        "user_id": user_id,
        "project_id": project_id,
        "conversation_id": convo_id,
        "conversation_valid": convo_valid,
        "conversations": project_convos,
    })
    resp.set_cookie(key="omicsbase_user_id", value=user_id, max_age=86400 * 7, path="/", samesite="lax")
    user_theme = (data.theme if data and hasattr(data, "theme") and data.theme else "dark").lower()
    resp.set_cookie(key="omicsbase_theme", value=user_theme, max_age=86400 * 7, path="/", samesite="lax")
    if AUTH_SECRET:
        signed_tok = jwt.encode({"sub": user_id, "project_id": project_id}, AUTH_SECRET, algorithm="HS256")
        resp.set_cookie(key="omicsbase_user_token", value=signed_tok, max_age=86400 * 7, path="/", samesite="lax", httponly=True)
    return resp


@router.post("/api/omicsbase/conversations")
async def create_conversation(
    data: Optional[TicketRequest] = None,
    authorization: Optional[str] = Header(None),
):
    ticket = (data.ticket if data and data.ticket else None) or (authorization.replace("Bearer ", "") if authorization else None)
    if not ticket:
        raise HTTPException(status_code=401, detail="Ticket required")
    claims = verify_ticket(ticket)

    user_id = claims.get("sub")
    project_id = claims.get("project_id")
    if not user_id or not project_id:
        raise HTTPException(status_code=400, detail="Missing user_id or project_id in claims")

    project_dir = PROJECTS_ROOT / "users" / str(user_id) / str(project_id)
    project_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(project_dir, 0o777)
    except Exception:
        pass
    try:
        symlink_path = PROJECTS_ROOT / str(project_id)
        if not symlink_path.exists() and not symlink_path.is_symlink():
            symlink_path.symlink_to(f"users/{user_id}/{project_id}")
    except Exception:
        pass
    logger.info(f"Scoped project workspace created/verified at: {project_dir}")

    api_key = get_session_api_key()
    headers = {
        "Content-Type": "application/json",
        "X-Session-API-Key": api_key,
    }

    agent_engine = (data.agent_engine if data and data.agent_engine else "openhands").lower().strip()

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            settings_resp = await client.get(f"{AGENT_SERVER_URL}/api/settings", headers=headers)
            settings_resp.raise_for_status()
            settings = settings_resp.json()
            agent_settings = settings.get("agent_settings", {})

            secrets = {}
            if agent_engine in ("codex", "claude-code", "gemini-cli"):
                agent_settings.pop("agent", None)

            if agent_engine == "codex":
                agent_settings["agent_kind"] = "acp"
                agent_settings["acp_server"] = "codex"
                agent_settings["acp_command"] = ["npx", "-y", "@agentclientprotocol/codex-acp@1.10.0"]
                agent_settings["acp_model"] = (data.model if data and data.model else "gpt-5.5")
                okey = os.environ.get("OPENAI_API_KEY")
                if okey:
                    secrets["OPENAI_API_KEY"] = okey
            elif agent_engine == "claude-code":
                agent_settings["agent_kind"] = "acp"
                agent_settings["acp_server"] = "claude-code"
                agent_settings["acp_command"] = ["npx", "-y", "@agentclientprotocol/claude-agent-acp@0.63.0"]
                agent_settings["acp_model"] = (data.model if data and data.model else "opus[1m]")
                ckey = os.environ.get("ANTHROPIC_API_KEY")
                if ckey:
                    secrets["ANTHROPIC_API_KEY"] = ckey
            elif agent_engine == "gemini-cli":
                agent_settings["agent_kind"] = "acp"
                agent_settings["acp_server"] = "gemini-cli"
                agent_settings["acp_command"] = ["npx", "-y", "@google/gemini-cli@0.46.0", "--skip-trust", "--acp"]
                agent_settings["acp_model"] = (data.model if data and data.model else "gemini-3.8-flash")
                gkey = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_KEY") or os.environ.get("GOOGLE_API_KEY")
                if gkey:
                    secrets["GEMINI_API_KEY"] = gkey
                    secrets["GOOGLE_API_KEY"] = gkey
            else:  # "openhands" default
                agent_settings["agent_kind"] = "openhands"
                llm_settings = agent_settings.get("llm") or {}
                if os.environ.get("LLM_MODEL"):
                    llm_settings["model"] = os.environ["LLM_MODEL"]
                if os.environ.get("LLM_BASE_URL"):
                    llm_settings["base_url"] = os.environ["LLM_BASE_URL"]
                if os.environ.get("LLM_API_KEY"):
                    llm_settings["api_key"] = os.environ["LLM_API_KEY"]
                agent_settings["llm"] = llm_settings

            system_instructions = build_omicsbase_system_prompt(
                project_id=str(project_id),
                project_dir=str(project_dir),
                agent_engine=agent_engine,
            )

            payload = {
                "workspace": {
                    "kind": "LocalWorkspace",
                    "working_dir": str(project_dir),
                },
                "agent_settings": agent_settings,
                "user_id": str(user_id),
                "agent_launch_additions": {
                    "system_message_suffix_append": system_instructions,
                },
            }
            if secrets:
                payload["secrets"] = {
                    k: {"kind": "StaticSecret", "value": str(v)}
                    for k, v in secrets.items()
                    if v
                }

            resp = await client.post(
                f"{AGENT_SERVER_URL}/api/conversations",
                json=payload,
                headers=headers,
            )
            if resp.status_code >= 400:
                logger.error(f"Agent server returned {resp.status_code}: {resp.text}")
                raise HTTPException(status_code=resp.status_code, detail=f"Agent server error: {resp.text}")
            convo_data = resp.json()
            convo_id = convo_data.get("id")
            logger.info(f"Successfully launched Agent Server conversation {convo_id} ({agent_engine}) for project {project_id}")

            SESSION_REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
            session_file = SESSION_REGISTRY_DIR / f"{convo_id}.json"
            session_file.write_text(json.dumps({
                "conversation_id": convo_id,
                "user_id": str(user_id),
                "project_id": str(project_id),
                "project_dir": str(project_dir),
                "agent_engine": agent_engine,
                "created_at": time.time(),
            }))

            ret = JSONResponse(content={
                "status": "ok",
                "conversation_id": convo_id,
                "agent_engine": agent_engine,
            })
            ret.set_cookie(key="omicsbase_user_id", value=str(user_id), max_age=86400 * 7, path="/", samesite="lax")
            if AUTH_SECRET:
                signed_tok = jwt.encode({"sub": str(user_id), "project_id": str(project_id)}, AUTH_SECRET, algorithm="HS256")
                ret.set_cookie(key="omicsbase_user_token", value=signed_tok, max_age=86400 * 7, path="/", samesite="lax", httponly=True)
            return ret
        except httpx.RequestError as exc:
            logger.error(f"Failed to connect to agent-server at {AGENT_SERVER_URL}: {exc}")
            raise HTTPException(status_code=502, detail="Agent server unreachable")


@router.get("/api/omicsbase/conversations/{project_id}")
async def list_project_conversations(project_id: str, request: Request):
    user_id = get_authenticated_user(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    convos = _get_project_conversations(str(user_id), str(project_id))
    return JSONResponse(content={"project_id": project_id, "conversations": convos})
