# FOPP Prenatal Diet Analysis

This folder contains the Quarto analysis site for the prenatal diet study: maternal prenatal diet exposures and child serum metabolite outcomes across visits 4, 5, 6, and 7.

## Start Here

[Rendered report entry point](output/index.html) (redirects to [Study Overview](output/design/study_overview.html))

Key pages:

- [Study Overview](output/design/study_overview.html): Study design and report navigation
- [Analysis Plan](output/design/analysis_plan.html): Analysis questions and planned model structure
- [Covariate Diagnostics](output/design/covariate_diagnostics.html): Appendix-style covariate and model specification support
- [Primary Results](output/primary/primary_results.html): Main exposure-metabolite association results
- [Figures](output/primary/figures.html): Primary result figures
- [Supplementary Tables](output/secondary/supplementary_tables.html): Supporting tables
- [Exposure Atlas](output/secondary/exposure_atlas.html): Exposure distributions and exposure-level summaries
- [Clinical Characteristics](output/data/clinical_characteristics.html): Analytic sample characteristics
- [Data Summary](output/data/data_summary.html): Data availability and panel summaries
- [Data Quality & Assumptions](output/data/data_quality_assumptions.html): Data handling notes and assumptions

## Source Layout

- `code/design/`: Study overview, analysis plan, covariate diagnostics
- `code/primary/`: Primary results and figures
- `code/secondary/`: Supplementary tables, secondary figures, exposure atlas
- `code/data/`: Clinical characteristics, data summary, data quality notes
- `data/`: local input data files used by the reports
- `output/`: rendered HTML site (mirrors the `code/` layout)
- `output/results/`: saved model result objects used by report pages

## Rendering

From this folder's `code/` directory, render one page at a time (Quarto website projects do not support multi-file `quarto render`):

```bash
for qmd in \
  design/study_overview.qmd \
  design/analysis_plan.qmd \
  design/covariate_diagnostics.qmd \
  primary/primary_results.qmd \
  primary/figures.qmd \
  secondary/supplementary_tables.qmd \
  data/clinical_characteristics.qmd \
  data/data_summary.qmd \
  data/data_quality_assumptions.qmd
do
  quarto render "$qmd"
done
```

To re-render the exposure atlas separately:

```bash
quarto render secondary/exposure_atlas.qmd
```

or in R:

```r
source("main.R")
```


## Reporting Notes

The covariate diagnostics page is intended as appendix material. Balance tests, exposure/covariate screening, covariate/outcome screening, beta-change checks, collinearity summaries, and missingness tables document modeling decisions and sensitivity-analysis choices. They should not be read as standalone evidence that a variable is or is not a confounder.

Rendered HTML in `output/` is generated from the Quarto sources in `code/`. When updating report language or analysis logic, edit the `.qmd` source first and then re-render the site.

`code/secondary/secondary_figures.qmd` is retained as an optional adaptation
source, but the canonical pipeline does not render or link it because that page
requires a separate `prenatal_diet_secondary_results.rds` artifact that the
canonical analysis does not produce. An adapted pack may re-enable the page only
when it also provides the corresponding secondary-analysis producer.
