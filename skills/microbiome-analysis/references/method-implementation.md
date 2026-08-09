# Method Implementation Contract

Use this reference when generating or reviewing microbiome downstream model scripts. The selected method must be explicit in `config/model_specification.csv`, the analysis script, the saved result object, and report text. Before implementing any branch, verify package versions and function signatures with `package-function-contract.md`.

## Pre-flight Requirements

- Align samples before filtering, transformation, modeling, or plotting.
- Record input scale: counts, relative abundance, CLR, or method-specific scale.
- Record package versions and function argument names for every package-backed method branch.
- Apply filtering before model fitting unless the plan explicitly defines model-specific feature sets.
- Write status rows for fitted, skipped, unavailable, and failed methods.
- Keep compositional limitations visible in reports and captions.

## Primary Data Construction

Use this stack for MetaPhlAn/HUMAnN3-style projects unless the project already uses a different object system:

1. Import MetaPhlAn profiles with `mia::importMetaPhlAn`.
2. Attach sample metadata to `colData`; stop if sample IDs are not aligned.
3. Remove unwanted features only by explicit rule, such as plasmid exclusion.
4. Convert MetaPhlAn profiles to relative abundance with `mia::transformAssay`.
5. Add observed richness and Shannon with `mia::addAlpha`.
6. Agglomerate by taxonomic rank with `mia::agglomerateByRanks`.
7. Create prevalent rank-specific alt experiments with `mia::agglomerateByPrevalence`, using explicit detection and prevalence thresholds.
8. Add HUMAnN3 path abundance or MetaCyc-like profiles as `SummarizedExperiment` alt experiments and transform them to relative abundance.

## Alpha Diversity Branches

Use two report-facing branches when the design contains a group and baseline/follow-up structure:

- Direct group/time tables: `stats::wilcox.test`, paired `wilcox.test` when subject pairing is valid, and `stats::p.adjust(method = "fdr" or "BH")`.
- Repeated-measures model: `lmerTest::lmer(metric ~ diet * duration + (1 | id))`, followed by `emmeans::emmeans` and `emmeans::contrast` for within-group and across-group contrasts.

Record metric, test/model, formula, term, estimate/statistic, p-value, q-value when applicable, sample count, and status.

## Beta Diversity / PERMANOVA Branches

Use this branch for community composition:

- Composition summaries: `miaViz::plotAbundance` at planned ranks, top taxa collapsed to `Other`.
- CLR heatmap: `mia::transformAssay(method = "clr", pseudocount = TRUE)`, row standardization, and `sechm::sechm`.
- Ordination: Bray dissimilarity on relative abundance, `scater::runMDS(..., FUN = mia::getDissimilarity, method = "bray")`, and `scater::plotReducedDim`.
- Stepwise divergence: `miaTime::addStepwiseDivergence` or the installed package that exports it after signature verification.
- PERMANOVA: prefer the existing project helper if present; otherwise use `vegan::adonis2` with explicit formula, permutations, seed, and dispersion/homogeneity reporting where available.

If `vegan` or the project PERMANOVA helper is unavailable, write an unavailable status row; do not replace PERMANOVA with an unrelated test.

## Differential Abundance Branches

### CLR Rank Tests

Use this for transparent report-facing summaries of prevalent taxa or features.

- Transform: relative abundance or profile table to CLR using the project helper or `log(x + pseudo_count)` centered within sample.
- Within time point: Kruskal or Wilcoxon across diet/group.
- Within group over time: paired Wilcoxon when IDs align at both time points.
- Output: feature, taxon/rank, comparison, CLR effect or CLR change, p-value, q-value, mean relative abundance, prevalence, n, method, and status.
- Interpretation: effect is on the CLR scale and mean abundance is relative abundance, not absolute abundance.

### MaAsLin3

Use this as the primary model-based DAA branch for the established workflow when package checks pass.

- Required package: `maaslin3`.
- Required signature check: confirm `maaslin3::maaslin3` has the arguments needed by the local call before running.
- Preferred settings: `normalization = "TSS"`, `transform = "LOG"`, `augment = TRUE`, `standardize = TRUE`, `max_significance = 0.25`, `plot_associations = FALSE`, `median_comparison_abundance = TRUE`, `median_comparison_prevalence = FALSE`, `verbosity = "WARN"`.
- Primary formula for repeated designs: `~ diet * duration + (1 | id)` or the project-specific exposure/time/subject equivalents.
- Within-group formula: `~ duration + (1 | id)` after stratifying by group.
- Across-group formula: `~ diet` after stratifying by time point.
- Output: preserve `all_results.tsv`, `significant_results.tsv`, `coef`, `qval_individual`, `qval_joint`, `model`, feature, metadata/term, and model status.

If MaAsLin3 is requested but unavailable or its API changed, write a status row and stop that branch. Do not silently substitute ANCOM-BC, ALDEx2, or CLR-LM.

## Optional Sensitivity Methods

Use ANCOM-BC, ALDEx2, DESeq2, or other alternatives only when explicitly requested or already present in the existing project. Guard every optional branch with package and signature checks, and label outputs as sensitivity or secondary unless the user changes the analysis plan.
