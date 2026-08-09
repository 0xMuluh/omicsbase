# Study Config Schema

Use this reference when creating a project-local microbiome config file. Every analysis project must use a declarative configuration file (`study_config.yml`) as the single source of truth. The agent must load this configuration dynamically in R/Python scripts; **hardcoding variable lists, paths, reference groups, filtering thresholds, or models directly in analysis scripts is strictly forbidden.**

## Required Sections

`study`: key, title, domain (`microbiome`), status.

`paths`: feature table, taxonomy table, metadata, derived object, result directory, report directories.

`identifiers`: sample ID, subject ID if separate, time/visit variable if longitudinal.

`features`: input scale, feature orientation, taxonomy rank, filtering thresholds, zero handling, transformation.

`variables`:
- `exposures`: list of exposures.
- `outcomes`: list of outcomes.
- `covariates_primary`: list of core adjustment variables.
- `covariates_sensitivity`: list of sensitivity covariates.
- `reference_levels`: mapping of reference levels for all categorical variables (e.g., control group or vehicle reference level).

`analyses`:
- `alpha_metrics`: list of alpha diversity metrics (Shannon, Simpson, etc.).
- `beta_metrics`: list of distance metrics (Bray-Curtis, Unifrac, etc.).
- `ordination_methods`: methods used (PCoA, t-SNE, etc.).
- `permanova_formula`: formula string for PERMANOVA.
- `diff_abundance_method`: package/method used (ANCOM-BC, MaAsLin3, etc.).
- `fdr_method`: FDR correction method.
- `random_seed`: numeric seed for rarefaction, permutations, and stochastic ordination.

`report`: pages and render command.

## Rules

- **Zero-Hardcoding Policy:** The R/Python analysis scripts must read all paths, variable lists, reference groups, filtering thresholds, and FDR scopes dynamically from this config file.
- **Reference Ordering:** Categorical covariates must have their reference groups declared explicitly in the config to ensure identical baseline comparisons across agent runs.
- **Assumptions & Seeds Preservation:** Record random seeds explicitly for rarefaction, permutations, and stochastic ordination methods. Mirror material choices in `config/decision_log.tsv` when they affect interpretation or reproducibility.
