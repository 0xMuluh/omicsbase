# Diversity Analysis

Use this reference when changing alpha diversity, beta diversity, ordination, or PERMANOVA logic. Read `package-function-contract.md` before changing package calls.

## Alpha Diversity

Primary metrics for the established workflow are observed richness and Shannon. Simpson can be added if requested by the plan. Confirm whether inputs are counts, MetaPhlAn relative abundance, rarefied counts, or an existing `TreeSummarizedExperiment`.

Prioritize:

- `mia::addAlpha(index = c("observed", "shannon"))` when working with a TSE object.
- `stats::wilcox.test` plus FDR for direct baseline, follow-up, and paired group tables.
- `lmerTest::lmer(metric ~ diet * duration + (1 | id))` plus `emmeans` when subject ID and repeated time points are present.

Report metric definition, sample count per group, model/test used, formula, covariates, p-value adjustment, and status.

## Beta Diversity

Primary branch:

- Bray dissimilarity on relative abundance.
- PCoA/MDS coordinates through the installed `mia`/`scater` function stack after signature checks.
- Composition plots at phylum and genus when ranks exist.
- CLR heatmap for selected or prevalent taxa when the report needs feature-level visual structure.

Report distance metric, transformation before distance calculation, ordination method, axes shown, and percentage variation explained where available.

## PERMANOVA

Check:

- formula and covariate order
- number of permutations
- seed or reproducibility setting
- strata or blocking if repeated measures or paired design exists
- dispersion or homogeneity output when group differences are interpreted

Do not interpret PERMANOVA group significance as location differences alone without checking dispersion when relevant.
