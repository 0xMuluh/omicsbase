# Report Output Contract

Use this reference when a metabolomics project must generate or repair report-facing outputs, QMD pages, tables, figures, and result-object consumers.

## Reference Implementations

Exemplar report sites live in `templates/metabolomics/` (FOPP studies). `prenatal_diet_metabolomics/code/` and `child_diet_metabolomics/code/` show the full page set for result-stage sites; `metabolites_neurocognition/code/` shows a planning-stage surface. Use them to match page organization, captions, and reporting language when the contract below leaves room for interpretation.

## Report Maturity Levels

A planning-stage report is acceptable only when final model outputs do not exist yet. It should include study overview, analysis plan, covariate/model diagnostics, clinical characteristics, and data summary.

A result-stage report must include the full scientific reporting surface when the analysis produces primary model outputs, sensitivity outputs, execution logs, and figures:

- study overview
- analysis plan
- covariate and model specification
- primary results
- figures
- supplementary tables
- secondary figures when secondary or group-level outputs exist
- exposure atlas when multiple exposure families are analyzed
- clinical characteristics
- data summary
- data quality and assumptions

## Result Object Patterns

Before writing or changing report pages, inspect the saved `.rds` object. Support these generic patterns:

### Age-Specific / Timing / Longitudinal Pattern

Expected components when the workflow includes age-specific windows, mutual timing, and longitudinal mixed models:

- `age_specific`
- `age_specific_sensitivity`
- `longitudinal`
- `longitudinal_sensitivity`
- `execution_log`
- `model_status`
- `model_manifest`

`model_status` should expose `question`, `analysis`, `exposure`, `timing`, `visit`, `status`, `reason`, complete-case ranges, features tested, and term family when available.

### Concurrent / Prospective / Repeated Pattern

Expected components when the workflow includes concurrent, prospective, and repeated-measures question families:

- `q1`, `q2`, `q3`, `q4`
- sensitivity counterparts for each primary question when sensitivity analysis is planned
- `model_status`

`model_status` should preserve question, analysis, exposure, status, reason, complete-case ranges, features tested, term family, exposure visit, and outcome visit when available.

### Secondary / Group-Score Pattern

Expected components when secondary biological group scores, interactions, or stratified outputs are included:

- group-score result tables by question or timing
- interaction and stratified result tables when planned
- `feature_map`
- group labels or group-column mappings

## Page-to-Artifact Mapping

- `study_overview.qmd`: result inventory, model family table, model-status summary, interpretation notes.
- `analysis_plan.qmd`: question list, scope, preprocessing, covariates, formulas, multiplicity, deliverables.
- `covariate_diagnostics.qmd`: covariate blocks, DAG/rationale, balance tests, collinearity, model-matrix checks, exposure-covariate screening, covariate-outcome screening, decision support, missingness, mathematical model forms.
- `clinical_characteristics.qmd`: table-one style summaries by visit/panel and relevant grouping variable.
- `data_summary.qmd`: sample coverage, visit overlap, exposure coverage, metabolite coverage, PCA/global structure, feature/cluster composition.
- `data_quality_assumptions.qmd`: missingness, sparsity, model coverage, transform feasibility, signal quality, participation, raw-vs-transformed checks.
- `primary_results.qmd`: question-specific inferential tables, sensitivity summary, top associations, execution status.
- `figures.qmd`: core analysis figures including sample structure, testing burden, signal summary, sensitivity stability, volcano/heatmap/timing/status figures.
- `supplementary_tables.qmd`: sample derivation, exposure definitions, model specs, result inventory, candidate associations, sensitivity stability, execution-status inventory.
- `secondary_figures.qmd`: extended figures, group-score figures, interaction/stratified figures, correlation heatmaps.
- `exposure_atlas.qmd`: per-exposure figure/table loops.

## Non-Negotiable Decisions

Preserve these in source code and captions:

- exact q-value threshold and FDR family
- complete-case rule and sample denominator for each model family
- exposure family grouping and timing labels
- model-status reasons for missing, skipped, failed, warned, singular, or rank-deficient fits
- whether figures are primary evidence, descriptive support, or post hoc exemplars
- whether sensitivity models add covariates, change filters, change transforms, or change sample definitions
