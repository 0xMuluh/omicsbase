# Generated Project Contract

Use this reference when the user provides raw metabolomics data plus a draft analysis plan and expects the agent to create the analysis project.

## Required Agent Output

Create or update these project-local artifacts before claiming the workflow is ready:

- `config/analysis_plan.R`: machine-readable study config with paths, identifiers, feature handling, variables, model defaults, report pages, and decision policy.
- `config/model_specification.csv`: one row per analysis family or model set; include scenario, exposure, outcome or feature set, covariate block, FDR family, minimum N, and primary/sensitivity status.
- `config/decision_log.tsv`: append-only record of every material decision made from the draft plan, raw data inspection, or explicit user instruction.
- `code/00_setup.R`: shared config loading, path handling, validation, file readers, and decision logging helpers.
- `code/01_prepare_data.R`: raw metadata and metabolite table inspection, ID alignment, feature orientation handling, transforms, filtering summaries, and derived object creation.
- `code/02_fit_models.R`: primary model execution, model-status logging, FDR correction, and saved result object creation.
- `code/03_validate_outputs.R`: result-object and report-consumer validation.
- Quarto source pages created with `quarto-research-report`: for planning-stage work create overview, analysis plan, covariate diagnostics, clinical characteristics, and data summary; for result-stage work create the full report surface described in `references/report-output-contract.md`.

Use `scripts/create_metabolomics_project.py` to copy the bundled project template when starting from an empty directory. Adapt the generated files to the specific draft plan instead of leaving unresolved generic values.

## Decision Capture Rules

Record a decision whenever the agent chooses or confirms any of the following:

- raw file path, delimiter, sheet, object, or extraction route
- subject ID, sample ID, visit variable, or visit mapping
- feature orientation, feature naming repair, excluded features, or feature class grouping
- transformation, pseudo-count, scaling, winsorization, or outlier rule
- exposure, outcome, covariate, stratification, interaction, or random-effect field
- model family, formula, reference level, complete-case rule, minimum sample size, and FDR family
- model-status category and reason for skipped, failed, or unstable models
- table columns, sorting, labels, captions, figure aesthetics, and report interpretation language

Do not bury these decisions only in prose. They must be represented in config, model spec, script logic, result objects, or the decision log.

## Minimum Result Object

A generated metabolomics result object should be an `.rds` list with these components when applicable:

- `plan`: loaded `analysis_plan` list or a sanitized copy
- `model_specification`: model-spec table used for the run
- `feature_map`: original feature names and analysis-safe column names
- `data_summary`: sample counts, feature counts, missingness, and filtering summaries
- `results`: long-format model results with `analysis_id`, `scenario`, `model_family`, `feature`, `exposure`, `term`, `estimate`, `std.error`, `statistic`, `p.value`, `q.value`, `n`, and `status`
- `model_status`: one row per fitted, skipped, or failed feature/model with explicit reason
- `decisions`: material decisions imported from or appended to `config/decision_log.tsv`

If a report page needs a field that is not present, update the analysis script and result object before changing the QMD table to work around the absence.

## Stop Or Continue

Continue with a generated skeleton when missing values are logistical, such as paths not yet filled, report title wording, or optional sensitivity analyses.

Stop and ask the user when the missing item changes the scientific estimand or validity of the model:

- exposure, outcome, or covariate role is ambiguous
- repeated-measures, pairing, or visit timing is unclear
- transform or scale is unknown and materially affects interpretation
- feature table and metadata cannot be aligned by IDs
- requested conclusion requires causal language not supported by the draft plan
