"""Generator service — LLM generates the Quarto project file-by-file."""

from __future__ import annotations

import json
import logging
from html import escape
from pathlib import Path
from typing import Any, Callable

import yaml

from app.schemas.schemas import AnalysisPlan
from app.services.file_inspector import format_file_summary_for_llm
from app.services.llm import call_llm, load_system_prompt
from app.services.recipe_engine import materialize_recipe_project

logger = logging.getLogger(__name__)

# The generation order: each step produces one file, and subsequent steps
# see all previously generated files as context.
GENERATION_STEPS = [
    {"id": "scaffold", "filename": "README.md", "prompt_name": None, "label": "Creating project scaffold"},
    {"id": "index_qmd", "filename": "code/index.qmd", "prompt_name": None, "label": "Creating report entry page"},
    {"id": "data_r", "filename": "code/data.R", "prompt_name": "generator_data", "label": "Generating data.R"},
    {"id": "funct_r", "filename": "code/funct.R", "prompt_name": "generator_functions", "label": "Generating funct.R"},
    {"id": "qmd_pages", "filename": None, "prompt_name": "generator_qmd", "label": "Generating analysis pages"},
    {"id": "quarto_yml", "filename": "code/_quarto.yml", "prompt_name": "generator_quarto_yml", "label": "Generating _quarto.yml"},
    {"id": "main_r", "filename": "code/main.R", "prompt_name": "generator_main", "label": "Generating main.R"},
    {"id": "readme", "filename": "README.md", "prompt_name": "generator_readme", "label": "Generating README.md"},
]


async def generate_project(
    project_dir: str,
    plan: AnalysisPlan,
    file_summaries: list[dict],
    uploaded_file_paths: dict[str, list[str]],
    study_manifest: dict[str, Any] | None = None,
    progress_callback: Callable[[str, str, dict[str, Any] | None], None] | None = None,
) -> list[str]:
    """Generate the entire Quarto project from an approved analysis plan.

    Args:
        project_dir: Path to the project directory.
        plan: The approved analysis plan.
        file_summaries: File inspection summaries.
        uploaded_file_paths: Map of role → file paths for uploaded files.
        study_manifest: Deterministic uploaded-study contract.
        progress_callback: Optional callback(step_id, status, metadata) for progress updates.

    Returns:
        List of generated file paths.
    """
    base = Path(project_dir)
    code_dir = base / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    (base / "data").mkdir(exist_ok=True)
    (base / "output").mkdir(exist_ok=True)

    system_prompt = load_system_prompt()
    plan_json = plan.model_dump_json(indent=2)
    file_desc = "\n\n".join(format_file_summary_for_llm(s) for s in file_summaries)

    # Track generated files for context accumulation
    generated_files: dict[str, str] = {}
    generated_paths: list[str] = []

    def _report(step_id: str, status: str, metadata: dict[str, Any] | None = None):
        if progress_callback:
            progress_callback(step_id, status, metadata)

    _report("scaffold", "running", {"detail": "Creating project folders and starter files"})
    scaffold_paths = _write_project_scaffold(base, plan, uploaded_file_paths)
    for scaffold_path in scaffold_paths:
        relative_path = _relative_project_path(base, scaffold_path)
        generated_paths.append(str(scaffold_path))
        if relative_path.startswith("code/"):
            generated_files[relative_path] = scaffold_path.read_text(errors="replace")
    _report("scaffold", "completed", {"detail": "Starter project is visible", "path": "README.md"})

    _report("recipes", "running", {"detail": "Binding validated analysis recipes"})
    recipe_result = materialize_recipe_project(
        project_dir=project_dir,
        plan=plan,
        study_manifest=study_manifest,
        uploaded_file_paths=uploaded_file_paths,
    )
    for recipe_file in recipe_result.get("files", []):
        recipe_path = Path(recipe_file)
        relative_path = _relative_project_path(base, recipe_path)
        if str(recipe_path) not in generated_paths:
            generated_paths.append(str(recipe_path))
        if relative_path.startswith("code/"):
            generated_files[relative_path] = recipe_path.read_text(errors="replace")
    materialized_step_paths = recipe_result.get("step_paths", {})
    _report(
        "recipes",
        "completed",
        {
            "detail": (
                f"Bound {len(recipe_result.get('recipe_ids', []))} deterministic recipe(s)"
                if recipe_result.get("recipe_ids")
                else "No deterministic recipe matched; using generator fallback"
            ),
            "recipes": recipe_result.get("recipe_ids", []),
        },
    )

    # --- Step 1: data.R ---
    if "code/data.R" in generated_files:
        _report("data_r", "completed", {"detail": "Using deterministic recipe data loader", "path": "code/data.R"})
    else:
        _report("data_r", "running", {"detail": "Generating data loader", "path": "code/data.R"})
        data_r = await _generate_file(
            system_prompt=system_prompt,
            plan_json=plan_json,
            file_descriptions=file_desc,
            uploaded_file_paths=uploaded_file_paths,
            target_file="data.R",
            instruction=_DATA_R_INSTRUCTION,
            generated_context=generated_files,
        )
        _write_file(code_dir / "data.R", data_r)
        generated_files["code/data.R"] = data_r
        generated_paths.append(str(code_dir / "data.R"))
        _report("data_r", "completed", {"detail": "Wrote code/data.R", "path": "code/data.R"})

    # --- Step 2: funct.R ---
    if "code/funct.R" in generated_files:
        _report("funct_r", "completed", {"detail": "Using deterministic recipe helpers", "path": "code/funct.R"})
    else:
        _report("funct_r", "running", {"detail": "Generating reusable R helpers", "path": "code/funct.R"})
        funct_r = await _generate_file(
            system_prompt=system_prompt,
            plan_json=plan_json,
            file_descriptions=file_desc,
            uploaded_file_paths=uploaded_file_paths,
            target_file="funct.R",
            instruction=_FUNCT_R_INSTRUCTION,
            generated_context=generated_files,
        )
        _write_file(code_dir / "funct.R", funct_r)
        generated_files["code/funct.R"] = funct_r
        generated_paths.append(str(code_dir / "funct.R"))
        _report("funct_r", "completed", {"detail": "Wrote code/funct.R", "path": "code/funct.R"})

    # --- Step 3: QMD pages (parallelized with asyncio.gather) ---
    enabled_steps = [s for s in plan.workflow if s.enabled]
    unmaterialized_steps = [s for s in enabled_steps if s.id not in materialized_step_paths]

    for step in enabled_steps:
        step_id = f"qmd_{step.id}"
        if step.id in materialized_step_paths:
            display_path = materialized_step_paths[step.id]
            _report(
                step_id,
                "completed",
                {"detail": f"Using deterministic recipe page {display_path}", "path": display_path},
            )

    async def _process_step(step):
        step_id = f"qmd_{step.id}"
        subdir, filename = _step_to_qmd_path(step.id)
        qmd_dir = code_dir / subdir if subdir else code_dir
        qmd_dir.mkdir(parents=True, exist_ok=True)
        qmd_path = qmd_dir / filename
        display_path = _relative_project_path(base, qmd_path)
        _report(step_id, "running", {"detail": f"Generating {display_path}", "path": display_path})

        step_results = []
        if step.classification == "contested" and step.ensemble_methods:
            for method in step.ensemble_methods:
                method_filename = f"{step.id}_{method['id']}.qmd"
                method_path = qmd_dir / method_filename
                method_qmd = await _generate_file(
                    system_prompt=system_prompt,
                    plan_json=plan_json,
                    file_descriptions=file_desc,
                    uploaded_file_paths=uploaded_file_paths,
                    target_file=method_filename,
                    instruction=_QMD_INSTRUCTION.format(
                        step_name=f"{step.name} — {method['name']}",
                        step_id=step.id,
                        method_id=method["id"],
                        method_name=method["name"],
                        classification=step.classification,
                        extra="This is one method in a contested ensemble. Generate the analysis using ONLY this method.",
                    ),
                    generated_context=generated_files,
                )
                _write_file(method_path, method_qmd)
                method_relative_path = _relative_project_path(base, method_path)
                step_results.append((step_id, method_relative_path, str(method_path), method_qmd))
            
            comparison_filename = f"{step.id}_consensus.qmd"
            comparison_path = qmd_dir / comparison_filename
            comparison_qmd = await _generate_file(
                system_prompt=system_prompt,
                plan_json=plan_json,
                file_descriptions=file_desc,
                uploaded_file_paths=uploaded_file_paths,
                target_file=comparison_filename,
                instruction=_CONSENSUS_INSTRUCTION.format(
                    step_name=step.name,
                    step_id=step.id,
                    methods=", ".join(m["name"] for m in step.ensemble_methods),
                ),
                generated_context=generated_files,
            )
            _write_file(comparison_path, comparison_qmd)
            comparison_relative_path = _relative_project_path(base, comparison_path)
            step_results.append((step_id, comparison_relative_path, str(comparison_path), comparison_qmd))
            return step_results
        else:
            qmd_content = await _generate_file(
                system_prompt=system_prompt,
                plan_json=plan_json,
                file_descriptions=file_desc,
                uploaded_file_paths=uploaded_file_paths,
                target_file=filename,
                instruction=_QMD_INSTRUCTION.format(
                    step_name=step.name,
                    step_id=step.id,
                    method_id="",
                    method_name="",
                    classification=step.classification,
                    extra="",
                ),
                generated_context=generated_files,
            )
            _write_file(qmd_path, qmd_content)
            return [(step_id, display_path, str(qmd_path), qmd_content)]

    if unmaterialized_steps:
        nested_results = await asyncio.gather(*[_process_step(s) for s in unmaterialized_steps])
        for step_results in nested_results:
            for step_id, display_path, abs_path, qmd_content in step_results:
                generated_files[display_path] = qmd_content
                if abs_path not in generated_paths:
                    generated_paths.append(abs_path)
                _report(step_id, "completed", {"detail": f"Finished page {display_path}", "path": display_path})

    # --- Step 4: deterministic _quarto.yml ---
    _report("quarto_yml", "running", {"detail": "Writing deterministic Quarto configuration", "path": "code/_quarto.yml"})
    qmd_pages = sorted(
        [
            path.removeprefix("code/")
            for path in generated_files
            if path.startswith("code/") and path.endswith(".qmd")
        ],
        key=_qmd_sort_key,
    )
    navbar = _build_quarto_navigation(qmd_pages)
    quarto_yml = yaml.safe_dump(
        {
            "project": {
                "type": "website",
                "output-dir": "../output",
                "execute-dir": "project",
                "render": qmd_pages,
            },
            "website": {
                "title": plan.project_name,
                "search": True,
                "navbar": {"left": navbar},
            },
            "format": {
                "html": {
                    "theme": "cosmo",
                    "toc": True,
                    "toc-depth": 3,
                    "toc-expand": 1,
                    "code-fold": True,
                    "code-summary": "Show code",
                    "number-sections": True,
                    "page-layout": "full",
                    "lightbox": True,
                    "fig-responsive": True,
                }
            },
        },
        sort_keys=False,
    )
    _write_file(code_dir / "_quarto.yml", quarto_yml)
    generated_files["code/_quarto.yml"] = quarto_yml
    generated_paths.append(str(code_dir / "_quarto.yml"))
    _report("quarto_yml", "completed", {"detail": "Wrote code/_quarto.yml", "path": "code/_quarto.yml"})

    # --- Step 5: deterministic render entrypoint ---
    _report("main_r", "running", {"detail": "Writing render orchestrator", "path": "code/main.R"})
    main_r = """data_status <- system2("Rscript", "data.R")\nif (data_status != 0) stop("data.R failed")\nrender_status <- system2("quarto", c("render"))\nif (render_status != 0) stop("Quarto render failed")\n"""
    _write_file(code_dir / "main.R", main_r)
    generated_files["code/main.R"] = main_r
    generated_paths.append(str(code_dir / "main.R"))
    _report("main_r", "completed", {"detail": "Wrote code/main.R", "path": "code/main.R"})

    # --- Step 6: README.md was created with the deterministic scaffold ---
    _report("readme", "completed", {"detail": "Using deterministic project documentation", "path": "README.md"})

    return generated_paths


async def _generate_file(
    system_prompt: str,
    plan_json: str,
    file_descriptions: str,
    uploaded_file_paths: dict[str, list[str]],
    target_file: str,
    instruction: str,
    generated_context: dict[str, str],
) -> str:
    """Generate a single file using the LLM."""

    # Build context from previously generated files
    context_parts = []
    for path, content in generated_context.items():
        # Truncate very long files to avoid token limits
        truncated = content[:8000] if len(content) > 8000 else content
        context_parts.append(f"### {path}\n```\n{truncated}\n```")
    context_text = "\n\n".join(context_parts) if context_parts else "(No files generated yet)"

    # Build file path mapping for data.R to know where files are
    path_mapping = "\n".join(
        f"  {role}[{index}]: ../data/{Path(path).name}"
        for role, paths in sorted(uploaded_file_paths.items())
        for index, path in enumerate(paths, start=1)
    )

    user_prompt = f"""## Task

Generate the file `{target_file}` for a microbiome analysis Quarto project.

{instruction}

## Analysis Plan

```json
{plan_json}
```

## Uploaded Data Files

{file_descriptions}

## File Paths (relative to code/ directory)

{path_mapping}

## Previously Generated Files

{context_text}

## Output

Return ONLY the file content. No markdown fences, no explanation. Just the raw file content that should be written to `{target_file}`.
"""

    response = await call_llm(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=16000,
    )

    # Strip markdown code fences if present
    content = response.strip()
    if content.startswith("```"):
        first_newline = content.index("\n")
        content = content[first_newline + 1:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

    return content


def title_case_step_id(step_id: str) -> str:
    """Return a readable workflow step name from an identifier."""
    return step_id.replace("_", " ").title()


def _relative_project_path(base: Path, path: Path) -> str:
    """Return a POSIX-style path relative to the generated project root."""
    return path.relative_to(base).as_posix()


def _qmd_sort_key(path: str) -> tuple[int, int, str]:
    section_order = {
        "index.qmd": 0,
        "design": 1,
        "primary": 2,
        "secondary": 3,
        "data": 4,
    }
    page_order = {
        "index.qmd": 0,
        "design/study_overview.qmd": 0,
        "design/analysis_plan.qmd": 1,
        "primary/alpha_diversity.qmd": 0,
        "primary/beta_diversity.qmd": 1,
        "primary/permanova.qmd": 2,
        "primary/differential_abundance_limrots.qmd": 3,
        "primary/metabolite_panel.qmd": 0,
        "primary/longitudinal_models.qmd": 1,
        "data/data_summary.qmd": 0,
    }
    section = path.split("/", 1)[0]
    return (
        section_order.get(path, section_order.get(section, 5)),
        page_order.get(path, 99),
        path,
    )


def _build_quarto_navigation(qmd_pages: list[str]) -> list[dict[str, Any]]:
    navigation: list[dict[str, Any]] = []
    if "index.qmd" in qmd_pages:
        navigation.append({"text": "Home", "file": "index.qmd"})

    sections = (
        ("design", "Setup & Design"),
        ("primary", "Primary Analysis"),
        ("secondary", "Secondary Analysis"),
        ("data", "Data"),
    )
    for directory, label in sections:
        pages = [path for path in qmd_pages if path.startswith(f"{directory}/")]
        if not pages:
            continue
        navigation.append(
            {
                "text": label,
                "menu": [
                    {"text": _navigation_page_label(path), "file": path}
                    for path in pages
                ],
            }
        )

    uncategorized = [
        path
        for path in qmd_pages
        if path != "index.qmd" and "/" not in path
    ]
    if uncategorized:
        navigation.append(
            {
                "text": "Other",
                "menu": [
                    {"text": _navigation_page_label(path), "file": path}
                    for path in uncategorized
                ],
            }
        )
    return navigation


def _navigation_page_label(path: str) -> str:
    labels = {
        "design/study_overview.qmd": "Study overview",
        "design/analysis_plan.qmd": "Analysis plan",
        "data/data_summary.qmd": "Data summary",
        "primary/alpha_diversity.qmd": "Alpha diversity",
        "primary/beta_diversity.qmd": "Beta diversity",
        "primary/permanova.qmd": "PERMANOVA",
        "primary/differential_abundance_limrots.qmd": "LimROTS differential abundance",
        "primary/metabolite_panel.qmd": "Metabolite panel",
        "primary/longitudinal_models.qmd": "Longitudinal models",
    }
    return labels.get(path, title_case_step_id(Path(path).stem))


def _write_project_scaffold(
    base: Path,
    plan: AnalysisPlan,
    uploaded_file_paths: dict[str, list[str]],
) -> list[Path]:
    """Create deterministic starter files so the workspace becomes visible immediately."""
    code_dir = base / "code"
    output_dir = base / "output"
    code_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(exist_ok=True)

    uploaded_rows = "\n".join(
        f"- `{role}`: `{Path(path).name}`"
        for role, paths in sorted(uploaded_file_paths.items())
        for path in paths
    ) or "- No uploaded files were mapped yet."
    workflow_rows = "\n".join(
        f"- {step.name} (`{step.id}`)" for step in plan.workflow if step.enabled
    ) or "- No enabled workflow steps were found."

    readme = f"""# {plan.project_name}\n\nThis project is being generated by OmicsBase. Files appear here as each build step completes.\n\n## Question\n\n{plan.question}\n\n## Uploaded Data\n\n{uploaded_rows}\n\n## Planned Workflow\n\n{workflow_rows}\n\n## Layout\n\n- `data/` contains uploaded source files.\n- `code/design/` documents the study contract and analysis plan.\n- `code/primary/` contains primary analysis pages.\n- `code/secondary/` contains sensitivity and supporting analyses when requested.\n- `code/data/` contains data summaries and quality checks.\n- `output/` contains rendered report files and machine-readable results.\n"""

    index = f"""---\ntitle: \"{plan.project_name}\"\n---\n\n# {plan.project_name}\n\n## Research Question\n\n{plan.question}\n\n## Analysis Workflow\n\n{workflow_rows}\n\nUse the navigation above to move from study design and validated data through the primary analysis results.\n"""
    study_overview = f"""---\ntitle: "Study overview"\n---\n\n## Research question\n\n{plan.question}\n\n## Study contract\n\n- **Domain:** {plan.domain}\n- **Study type:** {plan.study_type}\n- **Grouping variable:** {plan.grouping_variable or "Not configured"}\n\n## Uploaded inputs\n\n{uploaded_rows}\n"""
    analysis_plan = f"""---\ntitle: "Analysis plan"\n---\n\n## Approved workflow\n\n{workflow_rows}\n\n## Reproducibility contract\n\nThe generated report separates source data, executable analysis code, derived results, and rendered pages. Deterministic recipes record their parameters and session information within each analysis page.\n"""

    readme_path = base / "README.md"
    index_path = code_dir / "index.qmd"
    study_overview_path = code_dir / "design" / "study_overview.qmd"
    analysis_plan_path = code_dir / "design" / "analysis_plan.qmd"
    preview_path = output_dir / "index.html"
    _write_file(readme_path, readme)
    _write_file(index_path, index)
    _write_file(study_overview_path, study_overview)
    _write_file(analysis_plan_path, analysis_plan)
    workflow_preview = "".join(
        f"<li><span></span>{escape(step.name)}</li>" for step in plan.workflow if step.enabled
    )
    preview_path.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="15">
  <title>{escape(plan.project_name)} · OmicsBase</title>
  <style>
    :root {{ color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }}
    body {{ margin: 0; background: #f8fafc; color: #18181b; }}
    main {{ max-width: 920px; margin: 0 auto; padding: 64px 32px; }}
    .eyebrow {{ color: #0f766e; font-size: 12px; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; }}
    h1 {{ margin: 12px 0 8px; font-size: clamp(30px, 5vw, 52px); line-height: 1.05; letter-spacing: -.04em; }}
    .question {{ max-width: 720px; color: #52525b; font-size: 17px; line-height: 1.7; }}
    .status {{ display: inline-flex; align-items: center; gap: 9px; margin-top: 28px; border: 1px solid #d4d4d8; border-radius: 999px; padding: 8px 12px; background: white; font-size: 13px; }}
    .pulse {{ width: 8px; height: 8px; border-radius: 50%; background: #14b8a6; animation: pulse 1.4s infinite; }}
    section {{ margin-top: 48px; border-top: 1px solid #e4e4e7; padding-top: 28px; }}
    h2 {{ font-size: 18px; }}
    ul {{ list-style: none; padding: 0; display: grid; gap: 12px; }}
    li {{ display: flex; align-items: center; gap: 12px; color: #52525b; }}
    li span {{ width: 9px; height: 9px; border: 2px solid #a1a1aa; border-radius: 50%; }}
    @keyframes pulse {{ 50% {{ opacity: .35; transform: scale(.8); }} }}
  </style>
</head>
<body>
  <main>
    <div class="eyebrow">OmicsBase</div>
    <h1>{escape(plan.project_name)}</h1>
    <p class="question">{escape(plan.question)}</p>
    <div class="status"><span class="pulse"></span>Building the validated analysis report</div>
    <section>
      <h2>Planned analysis</h2>
      <ul>{workflow_preview}</ul>
    </section>
  </main>
</body>
</html>
"""
    )
    return [
        readme_path,
        index_path,
        study_overview_path,
        analysis_plan_path,
        preview_path,
    ]


def _step_to_qmd_path(step_id: str) -> tuple[str, str]:
    """Map a workflow step ID to a subdirectory and filename."""
    mapping = {
        "import": ("data", "import.qmd"),
        "quality_control": ("data", "quality_control.qmd"),
        "alpha_diversity": ("primary", "alpha_diversity.qmd"),
        "beta_diversity": ("primary", "beta_diversity.qmd"),
        "permanova": ("primary", "permanova.qmd"),
        "differential_abundance": ("primary", "differential_abundance.qmd"),
        "normalization": ("data", "normalization.qmd"),
        "taxonomy_bars": ("primary", "taxonomy.qmd"),
        "sensitivity_analysis": ("secondary", "sensitivity_analysis.qmd"),
        "session_info": ("data", "session_info.qmd"),
    }
    return mapping.get(step_id, ("primary", f"{step_id}.qmd"))


def _write_file(path: Path, content: str):
    """Write content to a file, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    logger.info("Generated: %s (%d bytes)", path, len(content))


# --- Instruction templates ---

_DATA_R_INSTRUCTION = """Generate `data.R` — the data loading and preprocessing script.

This script should:
1. Load all required R packages
2. Read the uploaded data files from the `../data/` directory
3. Build the appropriate analysis object (phyloseq, TreeSummarizedExperiment, data.frame — whatever is appropriate for the data)
4. Set up grouping variables, factors, and any necessary preprocessing
5. Save the processed object as an RDS file for other scripts to load
6. Print a summary of the loaded data

Follow the style of existing projects: clean, well-organized, no unnecessary comments.
Source funct.R at the top if helper functions are needed.
"""

_FUNCT_R_INSTRUCTION = """Generate `funct.R` — shared helper functions for the analysis.

This script should contain:
1. All library() calls for packages used across the project
2. Comparison group definitions
3. Helper functions for statistical tests, plotting, and table formatting
4. Any project-specific utility functions

Keep functions focused and reusable. No unnecessary comments that restate what the code does.
"""

_QMD_INSTRUCTION = """Generate a Quarto analysis page for: {step_name}

Step ID: {step_id}
Method: {method_id} {method_name}
Classification: {classification}
{extra}

The QMD page should:
1. Have a descriptive title
2. Load the preprocessed data object (from data.R output)
3. Source funct.R for shared functions
4. Run the analysis with clear, well-structured R code chunks
5. Generate publication-quality figures with ggplot2
6. Include summary tables
7. Write scientific narrative text (NOT AI drivel — write like a careful analyst)
8. Use code-fold: true in the YAML header or rely on the project-level setting

Follow OmicsBase report style: substantial R code chunks that do real analysis,
interspersed with scientific narrative. Figures should be well-labeled with proper
axis labels, legends, and captions.
"""

_CONSENSUS_INSTRUCTION = """Generate a consensus/comparison page for the contested step: {step_name}

Step ID: {step_id}
Methods compared: {methods}

This page should:
1. Load results from each individual method's analysis (from their saved outputs)
2. Compare which taxa/features are significant across methods
3. Generate a Venn diagram or UpSet plot showing overlap
4. Create a consensus table (significant in all methods) and a disagreement table (significant in only some)
5. Write a plain-language explanation of what the agreement/disagreement means
6. Include a section on method sensitivity interpretation
7. Flag any findings that depend entirely on method choice

This is the CORE DIFFERENTIATOR of the product. The narrative should be honest,
clear, and accessible to a non-computational biologist.
"""

_QUARTO_YML_INSTRUCTION = """Generate `_quarto.yml` — the Quarto website configuration.

Use the project type: website
Set output-dir to "../output"
Create a navbar with logical groupings of the analysis pages
Enable: toc, code-fold, cosmo theme, number-sections
Set appropriate figure dimensions and DPI

Reference the previously generated QMD files to build the navigation.
Use a clean Quarto website YAML structure with grouped navbar entries for design,
data, primary, and secondary pages when present.
"""

_MAIN_R_INSTRUCTION = """Generate `main.R` — the orchestration script.

This script should:
1. Source data.R to create/load the processed data object
2. Render all QMD files using quarto::quarto_render() or a simple quarto render call
3. Handle any parameterized renders if needed

If the project is simple enough that `quarto render` from the code/ directory
handles everything, main.R can just call quarto::quarto_render().
If parameterized renders are needed (for example alpha diversity across indices),
loop with lapply / purrr::map and pass parameters into quarto::quarto_render().
"""

_README_INSTRUCTION = """Generate `README.md` for the project.

Include:
1. Project title and description
2. Link to rendered report entry point
3. Source layout description
4. Rendering instructions
5. Data files description
6. Notes on the analysis approach
"""
