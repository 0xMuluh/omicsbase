# Differential Abundance

Use this reference when changing differential abundance models or report interpretation. Read `package-function-contract.md` and `method-implementation.md` before changing code.

## Method Priority

1. Preserve the existing project method if scripts, QMDs, outputs, or model specs already define it.
2. For MetaPhlAn/mia/TreeSummarizedExperiment workflows with diet/time or intervention questions, prioritize CLR rank-test summaries and MaAsLin3.
3. Use ANCOM-BC, ALDEx2, DESeq2, or other alternatives only as explicitly requested sensitivity methods or when the existing project already uses them.

Do not invent a compositional method just because it is common in microbiome literature. The skill must preserve the workflow implied by the project and analysis plan.

## Required Decisions

Document these in config, model spec, scripts, result objects, report text, or the decision log:

- input scale
- filtering thresholds
- taxonomic rank or feature family
- zero handling and CLR pseudo-count
- MaAsLin3 normalization and transform settings
- formula, reference levels, covariates, random effects, and stratification
- paired/repeated-measures handling
- multiple-testing correction and correction family
- output table columns and sorting rules

## Output Contract

Differential abundance result tables should expose stable fields such as:

- feature or taxon identifier
- taxonomic rank and lineage if available
- contrast or tested coefficient
- estimate/effect size with scale label
- standard error or interval if available
- p-value and adjusted p-value
- prevalence or mean abundance summary
- model note/status when failed, unavailable, or filtered

## Interpretation

Do not over-interpret isolated taxa without checking taxonomy quality, prevalence, abundance, and model stability. State when findings are exploratory, sensitivity-only, or on the CLR/relative-abundance scale.
