# Model Families

Use this reference when changing model logic, interpreting outputs, or checking whether report labels match model families.

## Age-Specific Or Cross-Sectional Models

Use when an exposure and metabolite panel are measured at a specific visit or time window.

Check:

- exposure variable exists in the visit-specific metadata
- metabolite features are standardized in the intended visit frame
- complete-case sample size is reported
- `estimate`, confidence interval, `p.value`, and `q.value` are present
- FDR correction is applied within the intended analysis family

## Prospective Models

Use when exposure at an earlier visit predicts metabolites at a later visit.

Check:

- participants are linked across the required visits
- baseline metabolite adjustment is intentional when included
- sample size reflects the intersection of required visits
- report labels distinguish prospective from concurrent models

## Mutual-Timing Models

Use when early and late exposure windows are mutually adjusted.

Check:

- early and late exposure terms are both in the design matrix
- outputs clearly separate early-term and late-term estimates
- the paired exposure family is preserved with `exposure_pair`
- sensitivity summaries use the correct matching keys

## Repeated-Measures Mixed Models

Use when repeated exposure and metabolite observations are modeled across visits.

Check:

- the data are in long format
- subject ID is used for the random-intercept or repeated-measures structure
- visit is encoded consistently as numeric or factor
- interaction labels in report code match the fitted term names
- generic exposure columns such as `exposure_it` are mapped back to their source exposure family for reporting

Do not rely on fragile interaction-term regexes when direct term names are available. Prefer explicit term checks or a broad interaction check such as `grepl(":", term)` only when all interaction terms should be grouped together.

## Sensitivity Models

Use sensitivity models to test robustness, not to redefine the primary estimand unless the user explicitly changes the analysis plan.

Check:

- sensitivity covariates are listed separately from the primary covariate block
- primary and sensitivity outputs are compared using stable keys
- summaries report estimate shifts, direction changes, and FDR-threshold reclassification when available

## Implementation Reference

For concrete formulas, required config columns, package engines, output columns, and status rules, read `method-implementation.md`.
