---
name: microbiome-analysis
description: Use when creating, inspecting, modifying, or validating microbiome downstream analysis workflows from raw data or existing projects, including MetaPhlAn or HUMAnN3 profiles, ASV/OTU tables, sample metadata, generated analysis scripts, model specifications, TreeSummarizedExperiment or phyloseq objects, mia/miaViz workflows, alpha diversity, beta diversity, Bray PCoA, PERMANOVA, prevalence filtering, CLR rank tests, MaAsLin3 differential abundance, repeated-measures microbiome models, Quarto microbiome reports, and microbiome result debugging.
---

# Microbiome Analysis

Use this skill to generate, inspect, modify, validate, or explain microbiome downstream analysis pipelines and their report-facing outputs.

## Operating Rules

1. Inspect the local project layout and identify preprocessing, analysis, Quarto source, rendered output, and raw count/taxonomy files.
2. Preserve raw sequencing-derived files and sample metadata unless the user explicitly asks to transform them.
3. Treat sample/taxon alignment as a primary invariant. Do not fit or summarize models until sample IDs and taxonomy IDs are reconciled.
4. Keep compositional-data limitations visible. Avoid interpreting relative-abundance changes as absolute abundance changes unless the data support that claim.
5. Treat every material choice in scripts, QMD chunks, tables, figures, configs, result objects, and report text as a decision to preserve or explicitly change.
6. When no project exists, create the missing project machinery from the skill assets: config, model specification, analysis scripts, decision log, result contract, and report scaffold.
7. Use the package/function priority contract before writing method code. Verify installed package versions and function signatures instead of assuming an API is current.
8. Prefer the primary workflow stack unless the user or project plan explicitly requests a different method: MetaPhlAn/HUMAnN3 -> `mia`/`TreeSummarizedExperiment` -> relative abundance/CLR -> Wilcoxon/Kruskal/LMM/PERMANOVA -> MaAsLin3.
9. Treat ANCOM-BC, ALDEx2, DESeq2, and other alternatives as optional sensitivity branches only when requested or already present in the project. Do not promote them to primary methods without evidence.
10. Validate filtering, normalization/transformation, model family, package availability, function arguments, and multiple-testing scope using the automated validation test suite (e.g., `scripts/validate_analysis_output.R`) before claiming completion.
11. Enforce environment locks (e.g., `renv.lock`) and schema-mapping configurations to prevent dependency drift and variable mapping guesses.
12. Enforce strict scientific reporting standards: exclude all chatbot/conversational slop, double check that all code chunks use code-folding (`code-fold: true`), and include detailed session environment metadata (`sessionInfo()`).

## Workflow

1. Read the project `README.md`, `_quarto.yml`, data-loading scripts, preprocessing scripts, and shared analysis functions.
2. Inventory existing analysis decisions from scripts, QMDs, result objects, configs, tables, figures, reports, READMEs, and TODOs.
3. Read `references/package-function-contract.md`; run `scripts/check_r_package_contract.R` or equivalent R checks before creating or changing R method code.
4. If starting from raw data plus a draft plan, read `references/generated-project-contract.md`, then run or adapt `scripts/create_microbiome_project.py` to create the config, script, and model-spec skeleton.
5. Identify input types and locate the declarative schema mapping file (e.g., `schema_map.yml`) to define column associations. Do not guess.
6. Confirm sample IDs, taxonomy names, ranks, and feature orientation.
7. Read `references/scenario-decision-map.md` and select the deterministic scenario branch before writing model code.
8. Determine the analysis family: preprocessing, alpha diversity, beta diversity, ordination, PERMANOVA, differential abundance, functional profiling, or report integration.
9. Check prevalence filtering, zero handling, transformation, covariates, randomization/permutation settings, package/environment lock compatibility, and FDR correction.
10. Run automated validation checks (result-contract assertions), scan reports for placeholder strings (like `TODO` or `FIXME`), and confirm code-folding and session provenance are rendered.

## Scripts

Use `scripts/create_microbiome_project.py <target-dir> --title "Study title" --key study_key` to create a cold-start project skeleton from bundled templates.

Use `scripts/bootstrap_r_packages.R --manifest <manifest.csv> --output <status.tsv>` to check required R packages. Add `--install` only when the user approves installing missing packages.

Use `scripts/inspect_microbiome_inputs.R <file-or-dir>...` to summarize common microbiome input objects and tables.

Use `scripts/scan_microbiome_report.R <file-or-dir>...` to scan microbiome source files for method markers and unresolved placeholders.

Use `scripts/check_r_package_contract.R [--output path.tsv]` to record package versions, required functions, and current function argument names for the primary and optional microbiome method stack.

## References

Read `references/cold-start-workflow.md` when starting a new microbiome project or receiving unfamiliar microbiome files.

Read `references/generated-project-contract.md` before creating scripts, model specs, result objects, or report source from raw data and a draft analysis plan.

Read `references/package-bootstrap.md` before checking or installing R packages.

Read `references/raw-data-preprocessing.md` before converting raw feature/profile tables, MetaPhlAn outputs, HUMAnN3 outputs, taxonomy tables, or existing microbiome objects into analysis-ready derived objects.

Read `references/package-function-contract.md` before writing R analysis code, changing package calls, or judging whether a method implementation is current.

Read `references/scenario-decision-map.md` before selecting preprocessing, diversity, differential abundance, or deterministic default behavior for a microbiome scenario.

Read `references/study-config-schema.md` when creating a project-local study config; use `assets/microbiome_study_config.yml` as the template.

Read `references/decision-preservation.md` before changing analysis code, report code, tables, figures, configs, or result-object contracts.

Read `references/data-contract.md` when mapping counts, taxonomy, sample metadata, ranks, or object structure.

Read `references/preprocessing.md` when changing filtering, zero handling, normalization, transformations, or feature aggregation.

Read `references/diversity-analysis.md` when changing alpha diversity, beta diversity, ordination, or PERMANOVA logic.

Read `references/differential-abundance.md` when changing differential abundance models or interpretation.

Read `references/method-implementation.md` when generating or reviewing concrete microbiome method code for MetaPhlAn/HUMAnN3 ingestion, `mia`/TreeSummarizedExperiment processing, CLR rank tests, MaAsLin3, alpha diversity, beta diversity, or PERMANOVA.

Read `references/compositional-data-rules.md` when interpreting relative abundance, log-ratio transforms, or compositional model outputs.

Read `references/validation-checklist.md` before claiming a microbiome analysis or report change is complete.
