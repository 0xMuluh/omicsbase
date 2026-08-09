# New Report Workflow

Use this reference when creating a Quarto report site from scratch.

## Intake Questions

- What is the study title and short key?
- What domain skill should guide the analysis content?
- Which pages are needed for the first useful report?
- Where should source and rendered output live?
- Should code be shown, folded, or hidden?
- Are external scripts allowed in private/protected reports?

## Build Sequence

1. Use `scripts/create_quarto_report_skeleton.py <target-dir>` to copy the bundled Quarto site template, or use `--template metabolomics-full` for a result-stage metabolomics report with the full page set.
2. Replace generic pending-state text with study-specific scientific content from the draft plan, config, model specification, result objects, and decision log.
3. Add result-loading code only after the domain workflow defines the expected result objects; do not invent report-side contracts that the analysis scripts do not write.
4. Keep page names stable and update `_quarto.yml` render/navigation entries together.
5. Render once the source pages are coherent.

## Minimum Useful Pages

A cold-start scientific report usually benefits from:

- `study_overview.qmd`: research question, sample panels, visit/data map
- `analysis_plan.qmd`: model families, covariates, hypotheses
- `data_summary.qmd`: sample availability, feature availability, missingness
- `primary_results.qmd`: main tables once results exist
- `covariate_diagnostics.qmd`: model-specification support

Do not create decorative landing pages for analysis reports. The first page should be useful scientific content.
