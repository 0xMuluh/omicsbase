# Method Implementation Contract

Use this reference when generating or reviewing metabolomics model scripts. The method branch must be explicit in `config/model_specification.csv`, the analysis script, the saved result object, and report captions.

## Required Method Branches

### Feature-Wise Linear Scan

Use for cross-sectional, age-specific, prospective, or sensitivity feature scans when each metabolite feature is the response.

- Formula: `metabolite_feature ~ exposure + covariates`
- Preferred engine: `limma::lmFit` followed by `limma::eBayes(trend = TRUE)` on standardized feature rows.
- Fallback: `stats::lm` per feature only when `limma` is unavailable; record the fallback engine in results/status.
- Output: one row per feature and exposure term with `estimate`, confidence interval when available, `p.value`, `q.value`, `n`, `feature`, `exposure`, `term`, `analysis_id`, and `model_family`.
- FDR: apply within the intended analysis/exposure family unless the plan specifies a different family.

### Mutual-Timing Feature Scan

Use when two exposure windows are jointly adjusted to compare timing.

- Formula: `metabolite_feature ~ exposure_primary + exposure_secondary + covariates`
- Required config columns: `exposure`, `exposure_secondary`, and `exposure_pair`.
- Engine: `limma::lmFit` plus `eBayes`; do not fit early and late terms in separate models.
- Output: long-format rows with `term_role` distinguishing `primary_timing` and `secondary_timing`; preserve `exposure_pair`.
- FDR: adjust primary and secondary timing term families separately unless the plan specifies a shared family.

### Repeated-Measures Mixed Model

Use when metabolite features and exposure or timing are repeated across visits or time windows.

- Formula: `metabolite_feature ~ exposure * time_variable + covariates + (1 | subject_id)`.
- Required config columns: `exposure`, `random_effect`, and `time_variable`; fall back to `identifiers.subject_id` and `identifiers.visit` only when explicit and non-ambiguous.
- Engine: `lmerTest::lmer(REML = FALSE)` with `broom.mixed::tidy(effects = "fixed")`.
- Output: main exposure and exposure-by-time interaction rows, with `term_role` set to `main_exposure` or `exposure_time_interaction`, plus `n` and `n_ids`.
- Status: record missing packages, too-few observations, too-few subjects, constant features, failed fits, and non-estimable terms.

### Metabolite-To-Outcome Model

Use when metabolite features predict a clinical or other downstream outcome.

- Continuous outcome: `lm(outcome ~ metabolite_feature + covariates)`.
- Binary outcome: `glm(outcome ~ metabolite_feature + covariates, family = binomial())`.
- Survival outcome: `survival::coxph(Surv(time, event) ~ metabolite_feature + covariates)`.
- Required config columns: `outcome` and `outcome_type`; survival additionally requires `time` and `event`.
- Output: one row per metabolite feature with `outcome`, `feature`, `term_role = metabolite_predictor`, effect estimate, standard error, p-value, q-value, and status.

### Conditional Longitudinal Model

Use when a later metabolite panel is modeled against an earlier exposure while adjusting for the baseline value of the same metabolite.

- Formula: `metabolite_later ~ exposure_earlier + metabolite_baseline + covariates`.
- Required data structure: paired baseline/later feature columns or an explicit mapping between baseline and later feature names.
- If the scaffold cannot infer this structure, it must generate the model-spec row and stop with a clear status instead of approximating it with an unadjusted prospective model.

## Shared Status Rules

Every method branch must write status rows for fitted, skipped, unavailable, and failed models. Do not let failed features disappear from the audit trail.

Common status reasons:

- `missing_exposure`
- `missing_covariates`
- `missing_outcome`
- `missing_variables`
- `too_few_complete_cases`
- `too_few_subjects`
- `constant_feature`
- `constant_outcome`
- `unavailable` for missing method packages
- `failed` with the package or model error message

## Package Decisions

Package-dependent methods are allowed, but the dependency is part of the method contract. If a required package is not installed, report `unavailable` for that branch and preserve the intended method in the model specification. Do not silently replace `limma`, `lmerTest`, or survival models with an unrelated method.
