# FOPP Child Metabolites and Neurocognition Analysis

This folder contains the Quarto analysis site for the child metabolomics and neurocognition study: child serum metabolites at visits 4 (6 months), 5 (1 year), and 6 (2 years), with neurocognitive outcomes anchored at visit 6.

## Start Here

[Rendered report entry point](output/index.html)

Key pages:

- [Study Overview](output/study_overview.html): Research questions, analytic panels, and visit mappings.
- [Analysis Plan](output/analysis_plan.html): Planned model structure and statistical hypotheses.
- [Covariate Diagnostics](output/covariate_diagnostics.html): Covariate, missingness, and model-specification support.
- [Clinical Characteristics](output/clinical_characteristics.html): Analytic sample characteristics.
- [Data Summary](output/data_summary.html): Data availability and panel summaries.

## Source Layout

- `code/`: Quarto source files, render configuration, and analysis scripts.
- `data/`: Local input data files used by the reports.
- `output/`: Rendered HTML site.

## Rendering

Before rendering, build the aligned `MultiAssayExperiment` objects:

```bash
cd code
Rscript make_mae.R
```

This combines `FOPP_clinical_variables_child_cognition_20260527.sav` and `Fopp_childserum_all_visits_MASTER_090326.xlsx` into the MAE objects used by the site.

Then render the Quarto site from `code/`:

```bash
quarto render
```

This compiles the `.qmd` source files into HTML reports under `output/`.

## Reporting Notes

Rendered HTML in `output/` is generated from the Quarto sources in `code/`. When updating report language or analysis logic, edit the `.qmd` source first and then re-render the site.
