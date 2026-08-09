# Metabolomics Report Architecture

Use this reference when creating or repairing a full metabolomics analysis report site. It captures the reporting structure used by mature metabolomics workflows with model outputs, execution logs, covariate diagnostics, figures, and supplement-style tables.

## Reference Implementations

Canonical examples of this architecture live in `templates/metabolomics/` (FOPP study sites). Open the matching page there to imitate layout, organization, and language when a rule below is ambiguous:

- `prenatal_diet_metabolomics/code/design/` — study overview, analysis plan, covariate diagnostics
- `prenatal_diet_metabolomics/code/primary/` — primary results and figures
- `prenatal_diet_metabolomics/code/secondary/` — supplementary tables, secondary figures, exposure atlas
- `prenatal_diet_metabolomics/code/data/` — clinical characteristics, data summary, data quality and assumptions
- `child_diet_metabolomics/code/` — same layout for a child-diet study with visit-specific and longitudinal panels
- `metabolites_neurocognition/code/` — planning-stage surface (overview, plan, covariate diagnostics, clinical characteristics, data summary) plus results pages

The `templates/shared_scripts/v2/` scripts show how result objects (age-specific, sensitivity, longitudinal components) are built and saved for these report pages. Treat these as exemplars, not as code to copy verbatim.

## Report Stage Selection

Use the smaller planning report when no final result object exists yet. It should contain:

- `study_overview.qmd`
- `analysis_plan.qmd`
- `covariate_diagnostics.qmd`
- `clinical_characteristics.qmd`
- `data_summary.qmd`

Use the full result report once primary result objects, model-status logs, and figure/table outputs exist or are part of the requested deliverable. It should contain:

- `study_overview.qmd`
- `analysis_plan.qmd`
- `covariate_diagnostics.qmd`
- `primary_results.qmd`
- `figures.qmd`
- `supplementary_tables.qmd`
- `secondary_figures.qmd`
- `exposure_atlas.qmd`
- `clinical_characteristics.qmd`
- `data_summary.qmd`
- `data_quality_assumptions.qmd`

Do not collapse the full report into only overview, plan, data summary, primary results, and covariate diagnostics when the analysis includes result objects and diagnostics that naturally populate the larger page set.

## Page Contracts

| Page | Required purpose | Required source artifacts |
| --- | --- | --- |
| `study_overview.qmd` | Study-level map: research aim, analytic panels, exposure timing/families, covariate strategy, model families, multiple-testing scope, result inventory, execution status, interpretation notes. | analysis config, model specification or manifest, result object, model-status log, derived analysis object |
| `analysis_plan.qmd` | Written protocol: objective, scope, data processing steps, covariate strategy, model plan, multiplicity, deliverables, tools/session. | draft plan, config, model specification, decision log |
| `covariate_diagnostics.qmd` | Covariate and model specification: causal roles, DAG or equivalent rationale, covariate blocks, balance tests when relevant, collinearity/VIF, model-matrix rank checks, exposure-covariate screening, covariate-outcome screening, decision support, traceability, missingness, proposed formulas. | metadata, covariate config, model spec, screening outputs, decision log |
| `clinical_characteristics.qmd` | Table-one style clinical and sample summaries by visit/panel and relevant grouping variable. | metadata, derived analysis object, requested characteristic tables |
| `data_summary.qmd` | Sample coverage, visit overlap, exposure coverage, metabolite coverage, PCA/global structure, feature/cluster composition, clinical snapshot. | derived analysis object, feature metadata, metadata |
| `data_quality_assumptions.qmd` | Data-quality support: missingness, metabolite sparsity, model coverage by question, transform feasibility, sample-level signal quality, visit participation, feature-level missingness, raw-vs-transformed checks, PCA sanity checks. | raw/derived feature matrices, metadata, model-status or coverage summaries |
| `primary_results.qmd` | Primary inferential tables: signal summary, question-specific result sections, exposure-family tables, top associations, sensitivity summary, execution status. | saved result object, model-status log, sensitivity outputs, feature metadata |
| `figures.qmd` | Core figures: sample structure, complete-case coverage, testing burden, signal summary, sensitivity stability, volcano plots, heatmaps, timing comparisons, longitudinal/repeated model status, descriptive trajectories when relevant. | saved result object, model-status log, derived analysis object, feature metadata |
| `supplementary_tables.qmd` | Supplementary table package: analytic sample derivation, exposure definitions, primary model specifications, returned result inventory, candidate associations, primary-vs-sensitivity stability, execution status inventory. | config, model manifest/specification, result object, model-status log, sensitivity outputs |
| `secondary_figures.qmd` | Appendix-style figures: global screens, forest plots, timing/comparison figures, class composition, metabolite correlations, group-score or secondary result figures when the analysis includes them. | secondary result object if present, primary result object, feature metadata, derived analysis object |
| `exposure_atlas.qmd` | Exposure-by-exposure atlas with per-exposure summaries and figures. | result object, exposure map, feature metadata, derived analysis object |

## Question-Family Reporting Patterns

For age-specific, timing, or longitudinal metabolomics workflows, `primary_results.qmd` should preserve the question structure in the report. Examples of valid generic sections include:

- concurrent or age-specific associations by visit/window
- prospective associations from earlier exposure to later metabolite panel
- mutual-timing comparison with separate early-term and late-term outputs
- repeated-measures or longitudinal main-effect and interaction outputs
- sensitivity summary comparing primary and expanded adjustment sets
- execution status table from the saved model log

For reports with categorical and continuous exposure families, keep separate subsections for nutrients, continuous diet-quality scores, and categorical diet-quality classes or equivalent exposure groups. Do not merge these if the analysis and table logic keep them separate.

## Result-Object Expectations

The report must inspect the result object before building tables. Common mature metabolomics result-object patterns include:

- age/timing/longitudinal pattern: `age_specific`, `age_specific_sensitivity`, `longitudinal`, `longitudinal_sensitivity`, `execution_log`, `model_status`, `model_manifest`
- concurrent/prospective/repeated pattern: `q1`, `q2`, `q3`, `q4`, `q1_sensitivity`, `q2_sensitivity`, `q3_sensitivity`, `q4_sensitivity`, `model_status`
- secondary/group-score pattern: group-score tables, sex-interaction tables, stratified tables, `feature_map`, `group_labels`, and `group_cols`

Do not assume one result-object shape for every study. Detect the object names, then choose page sections and table builders that match the actual components.

## Navigation Rules

The full report navigation should put scientific orientation first, result pages next, and data/quality support under a data menu:

1. Study overview
2. Analysis plan
3. Covariate and model specification
4. Primary results
5. Figures
6. Supplementary tables
7. Secondary figures
8. Exposure atlas
9. Data menu: clinical characteristics, data summary, data quality and assumptions

Update `_quarto.yml` render and navigation entries together. A page listed in navigation must exist; a generated page must be listed when it is part of the deliverable.

## Interpretation Rules

- State the model family, sample set, exposure timing, covariate block, transform/scale, and FDR family near the relevant table or figure.
- Keep status and warning information visible; result rows produced by warned or singular fits are not the same as clean evidence.
- Mark post hoc exemplar plots as illustrative when exposures are selected by observed q-values.
- Keep sensitivity models as robustness summaries unless the analysis plan changes the primary estimand.
- Preserve threshold decisions, especially `q < 0.10` or any other reporting threshold, in captions and table logic.
