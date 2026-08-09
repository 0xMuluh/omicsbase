# Validation Checklist

Use this checklist before reporting that a metabolomics analysis change is complete.

## Structural & Environmental Checks

- Confirm the changed files are source files, not only rendered outputs.
- Confirm the study folder, render config, and result paths are consistent.
- Confirm raw/private data files were not modified unless requested.
- Confirm reusable helper functions were used where appropriate.
- Confirm the R session environment matches the active `renv.lock` or conda environment spec, and no undocumented package dependencies were added.

## Statistical & Automated Verification Checks

- Run the automated test suite (e.g., `scripts/validate_analysis_output.R` or equivalent R/Python validation scripts) to verify that generated result data frames match the required schemas.
- Confirm there are no unexpected `NA`, `NaN`, or infinite values in key statistics columns (`estimate`, `p.value`, `q.value`).
- Confirm complete-case rules and minimum sample thresholds are preserved or intentionally changed.
- Confirm covariates are resolved against actual data and missing covariates are logged.
- Confirm factor variables and reference levels are set intentionally.
- Confirm FDR correction scope matches the analysis family.
- Confirm sensitivity models do not replace primary models accidentally.

## Reporting & Quality Checks

- Run the report linter to confirm there are no unresolved `TODO`, `FIXME`, or draft placeholder markers in `.qmd` files or rendered HTML.
- Confirm table column names and plot labels use human-readable mapped names, not raw database column codes (e.g., use "Bayley composite cognitive" instead of `CCognition_indexpoints6`).
- Confirm code-folding is active (`code-fold: true` and `code-summary: "Show code"`) for R/Python code chunks to preserve transparency and traceability.
- Confirm that the report includes a session information block running `sessionInfo()` at the end of the document.

## Runtime Checks

Run the narrowest practical check:

- targeted R script or result-object inspection for logic changes
- affected Quarto page render for report changes
- full `quarto render` only when the blast radius warrants it

If validation cannot be run, state exactly what was not verified and why.
