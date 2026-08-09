# Decision Preservation

Use this reference before editing report prose, QMD chunks, table builders, figure builders, YAML, or rendered-report behavior.

## Core Rule

A Quarto report is not only presentation. It often contains analysis decisions. Treat QMD code chunks, table logic, figure logic, captions, headings, labels, and narrative interpretation as decision-bearing source.

## Decisions To Preserve

Preserve or explicitly update decisions encoded in:

- setup chunks and loaded result paths
- table column selection, labels, sorting, filters, and thresholds
- figure facets, axes, color/group mappings, annotations, and displayed thresholds
- section order and page navigation
- captions and interpretation text
- model-family labels and result grouping
- warnings, assumptions, provenance notes, and data-quality caveats
- code-folding, render options, external scripts, and output paths

## Editing Procedure

1. Read the affected QMD page and its setup chunk before editing any displayed text.
2. Trace tables and figures back to their source result object or data frame.
3. Preserve naming and labels that carry scientific meaning.
4. When changing wording, keep interpretation aligned with the model and design.
5. When changing a table or figure, update surrounding prose and captions if the interpretation changes.
6. Render or run the narrowest practical check.

## Stop Conditions

Stop and ask when a requested wording/table/figure change changes the estimand, primary result, sensitivity status, FDR denominator, covariate interpretation, or causal strength of the report.
