---
name: metabolomics-analysis
description: Use when creating, inspecting, modifying, or validating metabolomics analysis workflows from raw data or existing projects, including NMR/metabolite feature tables, clinical covariates, MultiAssayExperiment or SummarizedExperiment objects, generated analysis scripts, model specifications, mass-univariate models, longitudinal metabolomics models, FDR summaries, model status logs, Quarto result pages, sensitivity analyses, visit mappings, or result-object debugging.
---

# Metabolomics Analysis

Use this skill to generate, inspect, modify, validate, or explain metabolomics analysis pipelines and their report-facing outputs.

## Operating Rules

1. Inspect the local study layout before editing. Identify source code, raw data, generated outputs, result objects, and render configuration.
2. Preserve raw data files unless the user explicitly asks to rebuild or transform them. Treat `.sav`, `.xlsx`, `.rds`, `.RData`, and local cache files as sensitive project state.
3. Treat every material choice in scripts, QMD chunks, tables, figures, configs, result objects, and report text as a decision to preserve or explicitly change.
4. When no project exists, create the missing project machinery from the skill assets: config, model specification, analysis scripts, decision log, result contract, and report scaffold.
5. Prefer existing study helpers, shared R scripts, and established model conventions over new analysis patterns.
6. Distinguish source logic from rendered output. Edit `.R`, `.qmd`, config, or metadata sources before touching generated HTML.
7. Validate result-object shape and run the automated test suite (e.g., `scripts/validate_analysis_output.R`) before claiming an analysis is correct or updating report pages that consume model outputs.
8. Report uncertainty when model assumptions, covariate definitions, or result contracts are not visible from local files.
9. Enforce environment locks (e.g., `renv.lock`) and schema-mapping configurations to prevent dependency drift and variable mapping guesses.
10. Enforce strict scientific reporting standards: exclude all chatbot/conversational slop, double check that all code chunks use code-folding (`code-fold: true`), and include detailed session environment metadata (`sessionInfo()`).

## Workflow

1. Read the relevant `README.md`, `_quarto.yml`, analysis driver scripts, and shared helper scripts.
2. Inventory existing analysis decisions from scripts, QMDs, result objects, configs, tables, figures, reports, READMEs, and TODOs.
3. If starting from raw data plus a draft plan, read `references/generated-project-contract.md`, then run or adapt `scripts/create_metabolomics_project.py` to create the config, script, and model-spec skeleton.
4. Locate the data contract and the declarative schema mapping file (e.g., `schema_map.yml`) to define column associations. Do not guess.
5. Read `references/scenario-decision-map.md` and select the deterministic scenario branch before writing model code.
6. Determine the model family before editing: cross-sectional feature scans, prospective models, mutual timing models, repeated-measures mixed models, outcome models, sensitivity models, or descriptive diagnostics.
7. Check complete-case rules, environment lock compatibility, standardization/transformation formulas, FDR scope, and reference groups.
8. For report changes, inspect the `.rds` result object fields used by the `.qmd` page before changing table or figure logic.
9. Run the automated verification checks (e.g., result-contract assertions), scan reports for placeholder strings (like `TODO` or `FIXME`), and confirm code-folding and session provenance are rendered.

## Scripts

Use `scripts/create_metabolomics_project.py <target-dir> --title "Study title" --key study_key` to create a cold-start project skeleton from bundled templates.

Use `scripts/inspect_result_object.R <file.rds>` to inspect saved result objects before changing report consumers.

Use `scripts/check_result_contract.R <file.rds>` to check common result-table columns.

Use `scripts/scan_metabolomics_report.R <file-or-dir>...` to scan source/report files for unresolved placeholders and method markers.

## References

Read `references/cold-start-workflow.md` when starting a new metabolomics project or receiving an unfamiliar project with no reliable README.

Read `references/generated-project-contract.md` before creating scripts, model specs, result objects, or report source from raw data and a draft analysis plan.

Read `references/scenario-decision-map.md` before selecting model families, generated scripts, or deterministic default behavior for a metabolomics scenario.

Read `references/study-config-schema.md` when creating a project-local study config; use `assets/metabolomics_study_config.yml` as the template.

Read `references/decision-preservation.md` before changing analysis code, report code, tables, figures, configs, or result-object contracts.

Read `references/data-contract.md` when mapping input data, identifiers, visits, assays, exposures, outcomes, or covariates.

Read `references/model-families.md` when changing statistical model logic or interpreting model families.

Read `references/method-implementation.md` when generating or reviewing concrete model code for linear scans, mutual timing, repeated-measures mixed models, outcome models, or conditional longitudinal models.

Read `references/result-object-contract.md` when report pages consume saved model objects.

Read `references/report-output-contract.md` when generating or repairing report-facing QMD pages, figures, tables, result-object consumers, or full report navigation.

Read `references/validation-checklist.md` before claiming an analysis or report change is complete.
