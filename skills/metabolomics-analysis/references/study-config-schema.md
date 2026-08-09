# Study Config Schema

Use this reference when creating a project-local metabolomics config file. Every analysis project must use a declarative configuration file (`study_config.yml`) as the single source of truth. The agent must load this configuration dynamically in R/Python scripts; **hardcoding variable lists, paths, reference groups, or models directly in analysis scripts is strictly forbidden.**

## Required Sections

`study`: human label, short key, domain (`metabolomics`), and project status.

`paths`: raw metadata path, raw metabolomics path, derived object path, result directory, report source directory, report output directory.

`identifiers`: subject ID, sample ID if separate, visit variable, visit labels.

`features`: assay name, feature orientation, feature classes, ratio feature pattern, transform rule, scaling rule.

`variables`:
- `exposures`: list of dietary or environmental exposure variables.
- `outcomes`: list of outcomes separated by type (continuous, binary).
- `covariates_primary`: list of core adjustment variables.
- `covariates_measurement`: mapping of visit-specific fasting/measurement covariates.
- `covariates_sensitivity`: list of sensitivity covariates.
- `reference_levels`: mapping of reference levels for all categorical variables (e.g., vaginal mode of delivery as reference).

`models`: primary model families, sensitivity model families, complete-case rule, minimum sample size, FDR method, FDR scope.

`report`: pages to create, table/figure standards, render command.

## Rules

- **Zero-Hardcoding Policy:** The R/Python analysis scripts must read all paths, variable lists, reference groups, and FDR scopes dynamically from this config file.
- **Reference Ordering:** Categorical covariates must have their reference groups declared explicitly in the config to ensure identical baseline comparisons across agent runs.
- **Assumptions Preservation:** Record assumptions that an agent must not rediscover from memory. Mirror material choices in `config/decision_log.tsv` when they affect interpretation or reproducibility.
- Use relative paths when possible.
