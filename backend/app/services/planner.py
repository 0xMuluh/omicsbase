"""Planner service — sends file summaries + question + optional custom plan to LLM → analysis plan."""

from __future__ import annotations

import json
import logging
from app.config import settings
from app.schemas.schemas import AnalysisPlan, ClarificationAnswer, ClarificationQuestion, ClarificationRequest
from app.services.file_inspector import format_file_summary_for_llm
from app.services.llm import call_llm, load_system_prompt
from app.services.provider_errors import LLMProviderError
from app.services.recipe_registry import (
    format_recipes_for_llm,
    get_recipe,
    load_recipe_registry,
    resolve_recipe,
)
from app.services.registry import format_registry_for_llm, get_ensemble_methods
from app.services.study_manifest import format_manifest_for_llm
from app.services.spawner import (
    format_report_pack_catalog_for_llm,
    resolve_report_pack,
)

logger = logging.getLogger(__name__)


def _answers_by_id(clarifications: list[ClarificationAnswer] | None) -> dict[str, list[str]]:
    return {answer.id: answer.values for answer in (clarifications or [])}


def _grouping_from_answers(
    study_manifest: dict | None,
    answers: dict[str, list[str]],
) -> dict | None:
    """Resolve the grouping variable from manifest candidates or clarified answers."""
    candidates = (study_manifest or {}).get("grouping_candidates", [])
    chosen = (answers.get("grouping_variable") or [None])[0]
    if chosen:
        return next(
            (c for c in candidates if str(c.get("column")) == chosen),
            {"column": chosen, "levels": []},
        )
    return candidates[0] if candidates else {}


async def generate_plan(
    question: str,
    file_summaries: list[dict],
    notes: str | None = None,
    custom_plan_text: str | None = None,
    study_manifest: dict | None = None,
    clarifications: list[ClarificationAnswer] | None = None,
) -> AnalysisPlan | ClarificationRequest:
    """Generate an analysis plan from user question, uploaded files, and optional custom plan.

    When the study design cannot be inferred, returns a ClarificationRequest
    instead of guessing; answers are passed back as ``clarifications`` on re-run.
    """
    answers = _answers_by_id(clarifications)

    provider = settings.llm_provider.lower()

    from app.services.providers import is_configured

    if not is_configured(provider):
        logger.warning("No configured API key for %s; using deterministic fallback plan", provider)
        return _build_default_plan(
            question,
            file_summaries,
            notes,
            custom_plan_text,
            study_manifest,
            answers,
        )

    try:
        system_prompt = load_system_prompt()
        file_descriptions = [format_file_summary_for_llm(s) for s in file_summaries]
        registry_text = format_registry_for_llm()
        recipe_text = format_recipes_for_llm()
        manifest_text = format_manifest_for_llm(study_manifest)
        report_pack_text = format_report_pack_catalog_for_llm()

        user_plan_section = ""
        if custom_plan_text and custom_plan_text.strip():
            user_plan_section = f"""## Attached User Analysis Plan

IMPORTANT: The user has provided an existing, custom analysis plan below. 
You MUST treat this custom plan as the primary authority for the analysis workflow, methods, group definitions, and analytical decisions. Map their requested steps faithfully into the structured JSON schema.

```
{custom_plan_text.strip()}
```
"""

        clarifications_section = ""
        if answers:
            formatted = "\n".join(
                f"- {answer_id}: {', '.join(values)}"
                for answer_id, values in answers.items()
                if values
            )
            if formatted:
                clarifications_section = f"""## Answered Clarifications (final — do NOT re-ask)

The user has already answered these questions. Treat them as authoritative:
{formatted}
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

{clarifications_section}

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

## Available ReportPacks

Select the pack whose domain and methodological shape best match the approved workflow.
ReportPacks are adaptive source directories, not fixed study-input schemas.

{report_pack_text}

## Response Format

Return ONLY valid JSON (no markdown, no explanation outside the JSON).

If the study design CAN be determined, return an analysis plan matching this structure:

{{
  "project_name": "string — inferred from question or custom plan",
  "domain": "microbiome | metabolomics",
  "report_pack_id": "exact ID from Available ReportPacks or null",
  "capabilities": ["exact capability id(s) from the selected ReportPack"],
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

If the study design CANNOT be determined (e.g. which column defines the comparison groups, what the group levels are, or whether the comparison is two-group or longitudinal), do NOT guess. Return instead:

{{
  "needs_clarification": {{
    "message": "one short sentence explaining what is missing",
    "questions": [
      {{
        "id": "unique_slug",
        "prompt": "a concrete question with options",
        "options": ["option 1", "option 2"],
        "multiple": false,
        "allow_custom": true
      }}
    ]
  }}
}}

Rules for needs_clarification:
- Ask only what blocks a decision; never re-ask questions already answered above.
- Ask at most 2-4 questions; options must be concrete (real column names from the manifest, real method names).
- Never use needs_clarification when the design is determinable — a usable plan is always preferred.
"""

        from app.services.llm import resolve_target

        planner_provider, planner_model = resolve_target("planner")
        response = await call_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_format="json",
            max_tokens=4000,
            model_override=planner_model,
            provider_override=planner_provider,
        )

        clean = response.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1]
            if clean.endswith("```"):
                clean = clean.rsplit("```", 1)[0]
            clean = clean.strip()

        plan_data = json.loads(clean)

        if isinstance(plan_data, dict) and plan_data.get("needs_clarification"):
            request = _parse_clarification_request(plan_data["needs_clarification"])
            if request and request.questions:
                return request
            return _build_default_plan(
                question,
                file_summaries,
                notes,
                custom_plan_text,
                study_manifest,
                answers,
            )

        return _bind_recipes(AnalysisPlan(**plan_data))

    except LLMProviderError:
        logger.exception("LLM planning stopped because the configured provider is unavailable")
        raise
    except Exception:
        logger.exception("LLM planning failed; using deterministic fallback plan")
        return _build_default_plan(
            question,
            file_summaries,
            notes,
            custom_plan_text,
            study_manifest,
            answers,
        )


def _parse_clarification_request(data: dict) -> ClarificationRequest | None:
    """Build a validated ClarificationRequest from a parsed LLM needs_clarification payload."""
    if not isinstance(data, dict):
        return None
    raw_questions = data.get("questions") or []
    if not isinstance(raw_questions, list) or not raw_questions:
        return None

    questions: list[ClarificationQuestion] = []
    for raw in raw_questions[:4]:
        if not isinstance(raw, dict) or not raw.get("id") or not raw.get("prompt"):
            continue
        options = [str(o) for o in (raw.get("options") or [])][:8]
        questions.append(
            ClarificationQuestion(
                id=str(raw["id"])[:64],
                prompt=str(raw["prompt"])[:500],
                options=options,
                multiple=bool(raw.get("multiple")),
                allow_custom=bool(raw.get("allow_custom", True)),
            )
        )
    if not questions:
        return None
    return ClarificationRequest(
        message=str(data.get("message") or "A couple of quick decisions before I can build the plan.")[:500],
        questions=questions,
    )


def _clarification_request_for_fallback(
    study_manifest: dict | None,
) -> ClarificationRequest:
    """Fallback planner's questions when the grouping design is unresolved."""
    candidates = (study_manifest or {}).get("grouping_candidates", [])
    grouping_options = [
        f"{candidate.get('column')} ({', '.join(candidate.get('levels') or [])})"
        for candidate in candidates
    ]
    questions: list[ClarificationQuestion] = [
        ClarificationQuestion(
            id="grouping_variable",
            prompt="Which column in the metadata defines the groups you want to compare?",
            options=grouping_options,
            multiple=False,
            allow_custom=True,
        )
    ]
    if candidates:
        questions.append(
            ClarificationQuestion(
                id="differential_abundance_method",
                prompt="How should differential abundance be handled?",
                options=[
                    "Run all methods as an ensemble (recommended)",
                    "ANCOM-BC2 only",
                    "ALDEx2 only",
                    "DESeq2 only",
                    "LimROTS only",
                ],
                multiple=False,
                allow_custom=False,
            )
        )
    return ClarificationRequest(
        message="A couple of quick decisions before I can build the analysis plan.",
        questions=questions,
    )


def _apply_da_method_answer(
    plan: AnalysisPlan,
    da_answer: list[str] | None,
) -> AnalysisPlan:
    """Fill the differential abundance ensemble, narrowed to one method when chosen."""
    default_ensemble = get_ensemble_methods("differential_abundance") or []
    for step in plan.workflow:
        if step.id == "differential_abundance" and not step.ensemble_methods:
            step.ensemble_methods = default_ensemble
    if not da_answer:
        return plan
    choice = (da_answer or [""])[0]
    if choice.lower().startswith("run all") or not choice:
        return plan
    wanted = next(
        (method for method in default_ensemble
         if choice.lower().startswith(method["name"].lower())),
        None,
    )
    if not wanted:
        return plan
    for step in plan.workflow:
        if step.id == "differential_abundance":
            step.ensemble_methods = [wanted]
    return plan


def _build_default_plan(
    question: str,
    file_summaries: list[dict],
    notes: str | None = None,
    custom_plan_text: str | None = None,
    study_manifest: dict | None = None,
    answers: dict[str, list[str]] | None = None,
) -> AnalysisPlan | ClarificationRequest:
    """Generate a high-quality scientific rule-based analysis plan fallback."""
    from app.schemas.schemas import WorkflowStep

    answers = answers or {}
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

    grouping = _grouping_from_answers(study_manifest, answers)
    group_levels = grouping.get("levels", [])
    if not group_levels and not answers.get("grouping_variable"):
        return _clarification_request_for_fallback(study_manifest)

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

    return _apply_da_method_answer(
        _bind_recipes(AnalysisPlan(
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
        )),
        answers.get("differential_abundance_method"),
    )


def _bind_recipes(plan: AnalysisPlan) -> AnalysisPlan:
    """Validate explicit recipe bindings and fill deterministic aliases."""
    for step in plan.workflow:
        recipe = get_recipe(step.recipe_id) if step.recipe_id else None
        if recipe and recipe.get("domain") != plan.domain:
            recipe = None
        recipe = recipe or resolve_recipe(step.id, plan.domain)
        step.recipe_id = recipe.get("id") if recipe else None
    plan.recipe_registry_version = load_recipe_registry().get("version")
    try:
        pack = resolve_report_pack(plan.report_pack_id, domain=plan.domain)
    except Exception as exc:
        logger.warning(
            "Planner selected invalid ReportPack %r: %s; using domain default",
            plan.report_pack_id,
            exc,
        )
        pack = resolve_report_pack(None, domain=plan.domain)
    plan.report_pack_id = pack.pack_id if pack is not None else None
    if pack is not None and pack.capabilities and not plan.capabilities:
        # Preserve compatibility with older planner responses while making the
        # selected capability set explicit in the persisted plan.
        plan.capabilities = [item.capability_id for item in pack.capabilities]
    if pack is not None and plan.capabilities:
        from app.services.capability_contract import resolve_plan_capabilities

        resolve_plan_capabilities(pack, plan)
    return plan
