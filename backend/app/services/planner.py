"""Planner service — sends file summaries + question + optional custom plan to LLM → analysis plan."""

from __future__ import annotations

import json
import logging
from app.config import settings
from app.schemas.schemas import AnalysisPlan
from app.services.file_inspector import format_file_summary_for_llm
from app.services.llm import call_llm, load_system_prompt
from app.services.recipe_registry import (
    format_recipes_for_llm,
    get_recipe,
    load_recipe_registry,
    resolve_recipe,
)
from app.services.registry import format_registry_for_llm
from app.services.study_manifest import format_manifest_for_llm

logger = logging.getLogger(__name__)


async def generate_plan(
    question: str,
    file_summaries: list[dict],
    notes: str | None = None,
    custom_plan_text: str | None = None,
    study_manifest: dict | None = None,
) -> AnalysisPlan:
    """Generate an analysis plan from user question, uploaded files, and optional custom plan."""

    provider = settings.llm_provider.lower()
    provider_has_key = {
        "anthropic": bool(settings.anthropic_api_key and not settings.anthropic_api_key.startswith("sk-ant-...")),
        "openai": bool(settings.openai_api_key and not settings.openai_api_key.startswith("sk-...")),
        "gemini": bool(settings.gemini_api_key and not settings.gemini_api_key.startswith("AIza...")),
        "openrouter": bool(settings.openrouter_api_key and not settings.openrouter_api_key.startswith("sk-or-...")),
        "deepseek": bool(settings.openai_api_key and not settings.openai_api_key.startswith("sk-...")),
        "groq": bool(settings.groq_api_key and not settings.groq_api_key.startswith("gsk_...")),
        "grok": bool(settings.grok_api_key or settings.xai_api_key),
        "xai": bool(settings.grok_api_key or settings.xai_api_key),
        "ollama": True,
    }

    if not provider_has_key.get(provider, False):
        logger.warning("No configured API key for %s; using deterministic fallback plan", provider)
        return _build_default_plan(
            question,
            file_summaries,
            notes,
            custom_plan_text,
            study_manifest,
        )

    try:
        system_prompt = load_system_prompt()
        file_descriptions = [format_file_summary_for_llm(s) for s in file_summaries]
        registry_text = format_registry_for_llm()
        recipe_text = format_recipes_for_llm()
        manifest_text = format_manifest_for_llm(study_manifest)

        user_plan_section = ""
        if custom_plan_text and custom_plan_text.strip():
            user_plan_section = f"""## Attached User Analysis Plan

IMPORTANT: The user has provided an existing, custom analysis plan below. 
You MUST treat this custom plan as the primary authority for the analysis workflow, methods, group definitions, and analytical decisions. Map their requested steps faithfully into the structured JSON schema.

```
{custom_plan_text.strip()}
```
"""

        user_prompt = f"""## Task

You are generating an executable downstream analysis plan. The user has uploaded study files and described their research question. Your job is to:

1. Identify what each uploaded file contains (feature table, taxonomy, metadata, etc.)
2. Identify the study design and grouping variables
3. Build the analysis workflow (following the user's attached plan if provided)
4. Classify each step as "standard" or "contested" using the decision-point registry
5. For contested steps, specify the ensemble methods to run
6. Bind every supported workflow step to an exact recipe_id from the executable recipe registry

## Research Question

{question}

{f"## Additional Notes{chr(10)}{notes}" if notes else ""}

{user_plan_section}

## Uploaded Data Files

{chr(10).join(file_descriptions)}

## Validated Study Manifest

{manifest_text}

## Decision-Point Registry

```yaml
{registry_text}
```

## Executable Recipe Registry

{recipe_text}

## Response Format

Return ONLY valid JSON matching this structure (no markdown, no explanation outside the JSON):

{{
  "project_name": "string — inferred from question or custom plan",
  "domain": "microbiome | metabolomics",
  "study_type": "two_group_comparison | multi_group | longitudinal | other",
  "question": "the research question restated concisely",
  "detected_inputs": [
    {{"file": "filename", "role": "feature_table | taxonomy | metadata | analysis_plan | other", "format": "csv | tsv | qza | biom | excel | rds | text", "details": "brief description"}}
  ],
  "grouping_variable": "column name or null",
  "group_levels": ["level1", "level2"],
  "workflow": [
    {{
      "id": "step_id",
      "name": "Human-readable step name",
      "classification": "standard | contested",
      "recipe_id": "exact executable recipe ID or null",
      "enabled": true,
      "rationale": "why this step is included or why it is contested",
      "ensemble_methods": [{{"id": "method_id", "name": "Method Name", "r_package": "pkg"}}] | null,
      "parameters": {{}} | null
    }}
  ],
  "estimated_runtime_minutes": 5,
  "recipe_registry_version": "{load_recipe_registry().get('version')}",
  "notes": "note whether a custom user plan was attached and incorporated"
}}
"""

        response = await call_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_format="json",
            max_tokens=4000,
        )

        clean = response.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1]
            if clean.endswith("```"):
                clean = clean.rsplit("```", 1)[0]
            clean = clean.strip()

        plan_data = json.loads(clean)
        return _bind_recipes(AnalysisPlan(**plan_data))

    except Exception as e:
        logger.exception("LLM planning failed; using deterministic fallback plan")
        return _build_default_plan(
            question,
            file_summaries,
            notes,
            custom_plan_text,
            study_manifest,
        )


def _build_default_plan(
    question: str,
    file_summaries: list[dict],
    notes: str | None = None,
    custom_plan_text: str | None = None,
    study_manifest: dict | None = None,
) -> AnalysisPlan:
    """Generate a high-quality scientific rule-based analysis plan fallback."""
    from app.schemas.schemas import WorkflowStep

    detected = []
    if study_manifest:
        detected = [
            {
                "file": item.get("name", "data"),
                "role": item.get("role", "other"),
                "format": item.get("format", "unknown"),
                "details": f"{item.get('format', 'unknown')} dataset with {item.get('dimensions', {})}",
            }
            for item in study_manifest.get("files", [])
            if item.get("role") != "analysis_plan"
        ]
    else:
        for s in file_summaries:
            detected.append({
                "file": s.get("name", "data.tsv"),
                "role": "feature_table" if "observations" in s.get("dimensions", {}) else "metadata",
                "format": s.get("format", "tsv"),
                "details": f"{s.get('format', 'tabular')} dataset with {s.get('dimensions', {})}",
            })

    grouping_candidates = (study_manifest or {}).get("grouping_candidates", [])
    grouping = grouping_candidates[0] if grouping_candidates else {}
    group_levels = grouping.get("levels", [])
    study_type = (
        "two_group_comparison"
        if len(group_levels) == 2
        else "multi_group"
        if len(group_levels) > 2
        else "other"
    )

    domain = (study_manifest or {}).get("domain")
    if domain not in {"microbiome", "metabolomics"}:
        domain = "microbiome"

    if domain == "metabolomics":
        manifest_columns = [
            str(column).lower()
            for item in (study_manifest or {}).get("files", [])
            for column in item.get("columns", [])
        ]
        has_visit = any(
            "visit" in column or column in {"time", "timepoint", "wave"}
            for column in manifest_columns
        )
        workflow = [
            WorkflowStep(
                id="descriptive_inventory",
                name="Metabolomics Data Inventory",
                classification="standard",
                recipe_id="metabolomics.inventory",
                enabled=True,
                rationale="Validate sample identifiers and create a typed metabolite feature matrix.",
            ),
            WorkflowStep(
                id="linear_feature_scan",
                name="Cross-sectional Metabolite Panel",
                classification="standard",
                recipe_id="metabolomics.linear_feature_scan",
                enabled=bool(grouping.get("column")),
                rationale="Fit one validated feature-level model per metabolite with BH correction.",
            ),
            WorkflowStep(
                id="repeated_measures_mixed_model",
                name="Longitudinal Metabolite Models",
                classification="standard",
                recipe_id="metabolomics.repeated_measures_mixed_model",
                enabled=bool(grouping.get("column") and has_visit),
                rationale="Fit exposure-by-visit mixed models with a participant random intercept.",
            ),
        ]
        default_question = "Descriptive and feature-level analysis of the metabolomics study."
    else:
        workflow = [
            WorkflowStep(
                id="import",
                name="Microbiome Data Inventory",
                classification="standard",
                recipe_id="microbiome.inventory",
                enabled=True,
                rationale="Align sample identifiers and validate the abundance matrix.",
            ),
            WorkflowStep(
                id="alpha_diversity",
                name="Alpha Diversity Profiling",
                classification="standard",
                recipe_id="microbiome.alpha_diversity",
                enabled=True,
                rationale="Calculate observed richness, Shannon diversity, and Simpson diversity.",
            ),
            WorkflowStep(
                id="beta_diversity",
                name="Beta Diversity Ordination",
                classification="standard",
                recipe_id="microbiome.beta_diversity",
                enabled=True,
                rationale="Calculate Bray-Curtis distances and principal coordinates.",
            ),
            WorkflowStep(
                id="permanova",
                name="PERMANOVA Community Comparison",
                classification="standard",
                recipe_id="microbiome.permanova",
                enabled=bool(grouping.get("column")),
                rationale="Test group-associated community differences with dispersion diagnostics.",
            ),
            WorkflowStep(
                id="differential_abundance",
                name="LimROTS Differential Abundance",
                classification="contested",
                recipe_id="microbiome.limrots_differential_abundance",
                enabled=bool(grouping.get("column")),
                rationale="Run reproducibility-optimized differential abundance as a sensitivity method.",
            ),
        ]
        default_question = "Comparative analysis of microbiome composition and diversity."

    return _bind_recipes(AnalysisPlan(
        project_name="Omics Comparative Study",
        domain=domain,
        study_type=study_type,
        question=question or default_question,
        detected_inputs=detected,
        grouping_variable=grouping.get("column"),
        group_levels=group_levels,
        workflow=workflow,
        estimated_runtime_minutes=3,
        recipe_registry_version=load_recipe_registry().get("version"),
        notes=(
            "Generated using the deterministic fallback planner. "
            "Review the detected grouping variable before building."
            + (" A user-provided plan was attached and should be reviewed manually." if custom_plan_text else "")
        ),
    ))


def _bind_recipes(plan: AnalysisPlan) -> AnalysisPlan:
    """Validate explicit recipe bindings and fill deterministic aliases."""
    for step in plan.workflow:
        recipe = get_recipe(step.recipe_id) if step.recipe_id else None
        if recipe and recipe.get("domain") != plan.domain:
            recipe = None
        recipe = recipe or resolve_recipe(step.id, plan.domain)
        step.recipe_id = recipe.get("id") if recipe else None
    plan.recipe_registry_version = load_recipe_registry().get("version")
    return plan

