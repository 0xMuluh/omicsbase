# Validation Checklist

Use this checklist before reporting that a microbiome analysis change is complete.

## Data & Environmental Checks

- Confirm the session environment matches the active `renv.lock` or conda environment spec, and no undocumented packages were introduced.
- Confirm sample IDs align between microbiome data and metadata.
- Confirm feature orientation is correct.
- Confirm taxonomy rank and aggregation are intentional.
- Confirm filtering thresholds (e.g., minimum prevalence or abundance) and retained feature counts are reported.
- Confirm no raw sequencing-derived files were modified unless requested.

## Statistical & Automated Checks

- Run the automated validation script (e.g., `scripts/validate_analysis_output.R`) to verify result table dimensions and variable types.
- Confirm there are no unexpected `NA`, `NaN`, or infinite values in key statistics columns (`estimate`, `p.value`, `q.value`).
- Confirm transformation/normalization matches the model family.
- Confirm covariates and reference levels are correct.
- Confirm PERMANOVA permutation settings and dispersion checks where relevant.
- Confirm differential abundance p-values are adjusted within the intended family.
- Confirm repeated measures or paired designs are handled explicitly.

## Reporting & Quality Checks

- Run the report linter to confirm there are no unresolved `TODO`, `FIXME`, or draft placeholder markers in `.qmd` files or rendered HTML.
- Confirm table column names and plot labels use human-readable mapped names, not raw database column codes.
- Confirm relative abundance is not described as absolute abundance.
- Confirm ordination plots identify distance and transformation.
- Confirm code-folding is active (`code-fold: true` and `code-summary: "Show code"`) for R/Python code chunks to preserve transparency and traceability.
- Confirm that the report includes a session information block running `sessionInfo()` at the end of the document.

## Runtime Checks

Run the narrowest practical check: input inspection, method scan, targeted analysis script, or affected Quarto render. If validation cannot be run, state what was not verified.
