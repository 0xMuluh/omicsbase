# Generated Project Contract

Use this reference when the user provides raw microbiome data plus a draft analysis plan and expects the agent to create the downstream analysis project.

## Required Agent Output

Create or update these project-local artifacts before claiming the workflow is ready:

- `config/analysis_plan.R`: machine-readable study config with paths, identifiers, feature orientation, taxonomic rank, filtering, transformation, variables, analysis families, report pages, and decision policy.
- `config/model_specification.csv`: one row per analysis family; include scenario, method, response or feature set, exposure/group, covariate block, distance metric, FDR family, permutations or seeds, and primary/sensitivity status.
- `config/decision_log.tsv`: append-only record of material choices from the draft plan, raw input inspection, or explicit user instruction.
- `code/00_setup.R`: shared config loading, path handling, validation, file readers, and decision logging helpers.
- `code/01_prepare_data.R`: feature table, taxonomy, and metadata inspection; sample alignment; feature orientation handling; filtering; transformation notes; and derived object creation.
- `code/02_diversity.R`: alpha diversity, beta distance or ordination hooks, PERMANOVA status, and saved outputs.
- `code/03_differential_abundance.R`: CLR rank-test summaries, MaAsLin3 execution when package/function checks pass, and guarded optional sensitivity methods only when requested.
- `code/04_validate_outputs.R`: result-object and report-consumer validation.
- `report/code/_quarto.yml` and bundled Quarto source pages: study overview, analysis plan, data summary, diversity results, differential abundance results, and diagnostics or sensitivity pages as needed.

Use `scripts/create_microbiome_project.py` to copy the bundled project template when starting from an empty directory. Adapt the generated files to the specific draft plan instead of leaving unresolved generic values.

## Decision Capture Rules

Record a decision whenever the agent chooses or confirms any of the following:

- raw feature, taxonomy, metadata, or object path and parser
- sample ID, subject ID, visit/time variable, pairing variable, and sample exclusion rule
- feature orientation, taxonomic rank, taxonomy parser, and aggregation rule
- prevalence, abundance, zero handling, pseudo-count, rarefaction, normalization, or log-ratio transform
- alpha metric, beta distance, ordination method, PERMANOVA formula, permutations, and seed
- differential abundance method, package/function contract, model formula, covariate block, structural-zero handling, FDR family, and effect scale
- table columns, sorting, captions, plot aesthetics, and report interpretation language

Do not bury these decisions only in prose. They must be represented in config, model spec, script logic, result objects, or the decision log.

## Minimum Result Object

A generated microbiome result object should be an `.rds` list with these components when applicable:

- `plan`: loaded `analysis_plan` list or a sanitized copy
- `model_specification`: model-spec table used for the run
- `sample_summary`: sample counts, group counts, missingness, and dropped-sample reasons
- `feature_summary`: feature counts before and after filtering, prevalence, abundance, and rank summaries
- `alpha_diversity`: metric values and model/test summaries
- `beta_diversity`: distance metric metadata, ordination coordinates, PERMANOVA results, and status rows
- `differential_abundance`: long-format feature results with `analysis_id`, `scenario`, `method`, `feature`, `taxon`, `term`, `effect`, `std.error`, `statistic`, `p.value`, `q.value`, `n`, and `status`
- `model_status`: one row per analysis, feature, or model with explicit reason for fitted, skipped, unavailable, or failed status
- `decisions`: material decisions imported from or appended to `config/decision_log.tsv`

If a report page needs a field that is not present, update the analysis script and result object before changing the QMD table to work around the absence.

## Stop Or Continue

Continue with a generated skeleton when missing values are logistical, such as file paths, report title wording, or optional sensitivity analyses.

Stop and ask the user when the missing item changes the scientific estimand or validity of the model:

- sample IDs cannot be aligned
- taxonomic rank or feature orientation is ambiguous
- input scale is unknown and affects transformation or interpretation
- paired/repeated structure is unclear
- method choice is material and not specified
- requested interpretation treats relative abundance as absolute abundance
