# Scenario Decision Map

Use this reference before generating or changing metabolomics model code. Select the matching branch, then encode the choice in `config/model_specification.csv`, scripts, result objects, and QMD text.

## Universal Defaults

- Use model-specific complete cases unless the draft plan requires a shared complete-case cohort.
- Use Benjamini-Hochberg FDR unless the plan specifies another method; record the correction family explicitly.
- Do not infer covariates from variable names alone. Use the draft plan, data dictionary, or user confirmation.
- Preserve original feature names in `feature_map`; use analysis-safe names only inside formulas.
- Write a model-status row for every feature/model attempted, skipped, or failed.
- Treat sensitivity models as robustness checks unless the user explicitly changes the primary estimand.

## Scenario Branches

| Trigger in draft plan or data | Generated analysis family | Required generated decisions | Script behavior | Report/QMD output |
| --- | --- | --- | --- | --- |
| Raw metadata plus metabolite table, no model yet | `descriptive_inventory` | IDs, visits, feature orientation, feature scale, missing-value tokens | Generate config, inspect alignment, create data summary, stop before inferential claims | Study overview and data summary only |
| Exposure and metabolite panel measured at same visit/window | `linear_feature_scan` or `cross_sectional_feature_scan` | exposure term, covariate block, visit subset, transform/scaling, FDR family | Fit one model per feature with status logging and q-values | Primary result table, top associations, model-status summary |
| Earlier exposure predicts later metabolite panel | `prospective_feature_scan` | exposure visit, metabolite visit, participant-linking rule, optional baseline metabolite adjustment | Restrict to linked participants; fit feature scans with timing labels | Prospective timing labels and availability table |
| Early and late exposure windows mutually adjusted | `mutual_timing_feature_scan` | paired exposure variables, common covariates, term labels, exposure pair key | Fit both exposure terms in one design; report term-specific estimates | Separate early/late term summaries and paired comparison table |
| Repeated metabolite or exposure measurements across visits | `repeated_measures_mixed_model` | subject random effect, visit encoding, within-person structure, package choice | Prefer established mixed-model package if installed; otherwise generate guarded code that stops with required package and formula | Longitudinal methods text, visit interaction table, convergence/status table |
| Metabolites predict a clinical outcome | `metabolite_outcome_model` | outcome type, feature role, covariates, event/time handling if applicable | Choose linear/logistic/survival family from outcome type; never coerce outcome silently | Outcome model summary and interpretation at the outcome scale |
| Additional covariates, exclusions, or alternate transforms requested | `sensitivity_feature_scan` | sensitivity label, changed covariates/exclusions/transform, comparison keys | Run after primary; join by stable feature/exposure/term keys | Estimate-shift, sign-change, and FDR reclassification table |
| Strata or subgroup requested | `stratified_feature_scan` | stratum variable, minimum N per stratum, interaction vs separate-model choice | Prefer interaction model when a direct contrast is the question; otherwise fit per stratum with status rows | Stratum availability and contrast-specific summaries |

## Deterministic Fallbacks

If a requested branch is not fully implementable with installed packages, generate the project structure and guarded code anyway. The code must fail early with the missing package, formula, or design detail rather than silently switching to a different analysis.

If the draft plan conflicts with raw data structure, preserve both facts in the decision log and stop at the earliest script that proves the conflict.
