# Generated Report Contract

Use this reference when generating QMD source from a draft analysis plan or connecting domain-generated scripts to a report.

## Reference Implementations

Exemplar generated sites live in `templates/metabolomics/` (FOPP studies): `prenatal_diet_metabolomics/code/` and `child_diet_metabolomics/code/` for result-stage sites, `metabolites_neurocognition/code/` for planning-stage pages. When a page in this contract is ambiguous, read the matching `.qmd` there before writing.

## Required Pages

A cold-start planning report should create these source pages unless the analysis plan clearly says otherwise:

- `study_overview.qmd`: research question, population, raw data sources, sample/visit map, and planned outputs.
- `analysis_plan.qmd`: scenarios, model families, covariate blocks, filtering/transformation decisions, FDR families, and sensitivity plan.
- `data_summary.qmd`: sample availability, feature availability, missingness, filtering summaries, and dropped records.
- `primary_results.qmd`: validated primary result tables and figures from saved result objects.
- `covariate_diagnostics.qmd`: covariate completeness, factor levels, collinearity or sparsity checks when available.
- Add `sensitivity_results.qmd` or domain-specific pages only when the model specification includes them.

For a result-stage metabolomics report, do not stop at this minimum set. Read `metabolomics-report-architecture.md` and create the full report surface when result objects, model-status logs, primary figures, supplementary tables, secondary figures, exposure atlases, or data-quality diagnostics are part of the deliverable.

## Generated QMD Rules

- QMD pages must load the project config and result objects rather than duplicating model decisions in prose.
- Every table or figure must have a stable source: config, model specification, result object, or validation output.
- Early generated reports may contain empty-state sections, but the text must say what artifact is missing and which script creates it.
- Avoid final-sounding interpretation until the relevant result object has passed validation.
- Table labels should be human-readable, but source identifiers must remain recoverable from hidden columns, captions, or linked result objects.
- Captions must state the model family, sample set, transform/scale, covariate block, and FDR family when those decisions affect interpretation.
- Report navigation must match actual files. Update `_quarto.yml` whenever pages are added, renamed, or removed.

## Decision Preservation

When a report page is generated or changed, preserve these decisions in source, not just rendered HTML:

- chunk options and cache/render behavior
- data object paths and expected components
- table columns, filters, sort order, labels, and significance thresholds
- figure mappings, grouping, color/facet variables, and axis scale
- interpretation boundaries, including association vs prediction vs causal language
- known data limitations and reasons models were skipped

## Validation

Before reporting completion, render the affected page or explain why rendering was not practical. If rendering succeeds but result objects are not yet created, state that report structure was validated and analytical results remain pending.
