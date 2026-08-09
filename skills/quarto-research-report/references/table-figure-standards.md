# Table And Figure Standards

Use this reference when editing result tables, summary tables, plots, captions, labels, or supplementary outputs.

## Tables

Use human-readable column names in rendered reports.

Prefer:

- `Variable`
- `Family`
- `Available, n`
- `Beta`
- `95% CI`
- `P`
- `Q`
- `Complete cases, n`

Avoid exposing raw names such as `outcome_variable`, `non_missing_n`, or `q.value` unless the raw field name is the point of the table.

Use interactive tables only when pagination, filtering, or export controls help the reader. Small static tables should render as normal tables.

## Figures

Every figure should have a clear scientific role:

- sample flow or coverage
- model yield or testing burden
- effect-size distribution
- top-hit effect estimates
- overlap of signals across model families
- sensitivity stability
- data-quality or missingness diagnostics

Axis labels should state the unit or scale. Captions should explain what is plotted, not how the plotting code works.

## Result Labels

Ensure labels match the model family:

- concurrent
- prospective
- repeated-measures main effect
- repeated-measures interaction
- early-pregnancy term
- late-pregnancy term
- sensitivity model

Do not let implementation terms such as `exposure_it`, `x`, or `visit_factor7` leak into reader-facing labels unless the table is explicitly diagnostic.
