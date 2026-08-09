"""Auto-apply mid-job guidance after a background run completes."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.services.llm import call_llm
from app.services.provider_errors import LLMProviderError

logger = logging.getLogger(__name__)

GUIDANCE_SYSTEM_PROMPT = """You convert queued mid-job user guidance into one OmicsBase workspace action.
Return only JSON in one of these forms:
{"type":"action","action":"set_recipe_enabled|update_recipe_parameters|set_analysis_variables|run_recipe|edit_project","arguments":{},"instruction":"precise edit instruction if needed","message":"short status"}
{"type":"final","message":"why no change is needed"}

Prefer recipe/configuration actions over edit_project. Use edit_project only for narrative/layout/code wording changes.
Never invent recipe IDs or parameters that are not in the provided registry context.

Examples:
- "use only Shannon for alpha" → update_recipe_parameters
- "enable PERMANOVA" → set_recipe_enabled
- "rerun beta diversity" → run_recipe
- "fix the figure caption" → edit_project
"""


async def decide_guidance_action(project, guidance: str) -> dict[str, Any]:
    """Ask the LLM how to apply queued guidance against the current plan."""
    from app.services.recipe_intent import infer_recipe_action, prefer_recipe_over_edit

    plan = project.analysis_plan or {}
    prompt = f"""## Queued guidance
{guidance}

## Current analysis plan
```json
{json.dumps(plan, indent=2, default=str)[:12000]}
```

## Study domain
{(plan.get('domain') or (project.study_manifest or {}).get('domain'))}
"""
    try:
        raw = await call_llm(
            system_prompt=GUIDANCE_SYSTEM_PROMPT,
            user_prompt=prompt,
            response_format="json",
            max_tokens=1200,
        )
        text = raw.strip()
        if text.startswith("```"):
            first_newline = text.find("\n")
            if first_newline >= 0:
                text = text[first_newline + 1:]
            if text.endswith("```"):
                text = text[:-3]
        decision = json.loads(text.strip())
        if isinstance(decision, dict):
            return prefer_recipe_over_edit(project, guidance, decision)
    except LLMProviderError:
        raise
    except Exception as exc:
        logger.warning("Guidance decision failed, falling back to recipe heuristic or edit: %s", exc)

    recipe_decision = infer_recipe_action(project, guidance)
    if recipe_decision is not None:
        return recipe_decision

    return {
        "type": "action",
        "action": "edit_project",
        "instruction": guidance,
        "message": "I’ll apply your queued guidance and verify the report.",
    }
