# Package And Function Contract

Use this reference before writing, changing, or reviewing microbiome R code. The agent must know which packages and functions are primary, which are optional, and whether the installed function signatures still match the expected workflow.

## Verification Protocol

1. Inventory methods requested by the draft plan, existing QMDs, scripts, and model specification.
2. Run `scripts/check_r_package_contract.R --output <project>/results/package_function_contract.tsv` or reproduce its checks with `requireNamespace()`, `utils::packageVersion()`, `getExportedValue()`, and `formals()`.
3. Record package versions and function argument names in the decision log or result object.
4. If a required package or function is missing, write an unavailable status row for that branch. Do not substitute another method silently.
5. If a function exists but required arguments are absent or renamed, stop that method branch and update the code only after checking the installed documentation or local examples.

## Primary Workflow Stack

| Purpose | Package | Functions to prefer | Required checks |
| --- | --- | --- | --- |
| MetaPhlAn import | `mia` | `importMetaPhlAn` | Confirm `removeTaxaPrefixes` support or adapt after checking formals. |
| TSE object structure | `TreeSummarizedExperiment`, `SingleCellExperiment`, `SummarizedExperiment` | `TreeSummarizedExperiment`, `altExp`, `altExpNames`, `SummarizedExperiment` | Confirm alt experiments preserve sample order and colData. |
| Transform and aggregate | `mia` | `transformAssay`, `addAlpha`, `agglomerateByRanks`, `agglomerateByPrevalence`, `meltSE`, `getTop` | Confirm `assay.type`, `method`, `pseudocount`, `detection`, and `prevalence` arguments. |
| Composition plots | `miaViz`, `sechm`, `patchwork` | `plotAbundance`, `plotBoxplot`, `sechm`, `wrap_plots` | Confirm assay names and annotation variables exist. |
| Alpha tests | `stats`, `lmerTest`, `emmeans`, `sjPlot` | `wilcox.test`, `p.adjust`, `lmer`, `emmeans`, `contrast`, `tab_model`, `plot_model` | Use Wilcoxon/FDR for direct group tables; use `diet * duration + (1 | id)` for repeated-measures LMM when subject/time exist. |
| Beta diversity | `mia`, `scater`, `vegan` | `getDissimilarity`, `runMDS`, `plotReducedDim`, `getPERMANOVA`, `adonis2` | Prefer Bray distance on relative abundance for the established branch; check permutations and dispersion/homogeneity output. |
| CLR rank DAA | `mia`, `stats` | `transformAssay(method = "clr")`, `kruskal.test`, `wilcox.test`, `p.adjust` | Use CLR scale; report effect as CLR difference, not absolute abundance. |
| MaAsLin3 DAA | `maaslin3` | `maaslin3` | Confirm arguments before calling. Prefer `normalization = "TSS"`, `transform = "LOG"`, `augment = TRUE`, `standardize = TRUE`, `max_significance = 0.25`, `plot_associations = FALSE`. |
| Functional profiles | base R, `mia`, `SummarizedExperiment` | `read.csv`, `SummarizedExperiment`, `transformAssay` | Add HUMAnN3 path abundance and MetaCyc-like tables as alt experiments; keep sample order identical. |

## Optional Sensitivity Stack

Use these only when the draft plan explicitly requests them or the existing project already uses them:

| Method | Package | Rule |
| --- | --- | --- |
| ANCOM-BC/ANCOM-BC2 | `ANCOMBC`, `phyloseq` | Guard with package/signature checks; do not make this the default primary method for a MetaPhlAn/mia/MaAsLin3 workflow. |
| ALDEx2 | `ALDEx2` | Guard with package/signature checks; use for explicit Monte Carlo Dirichlet-multinomial sensitivity, not as an invented default. |
| DESeq2 | `DESeq2` | Use only with count-scale data and explicit caveats; never use for relative abundance tables. |

## Formula Priorities

For diet/intervention studies with baseline and follow-up:

- Primary interaction: `~ diet * duration + (1 | id)` or project-specific equivalents.
- Within-group time change: `~ duration + (1 | id)` after stratifying by diet.
- Across-group at one time point: `~ diet` after stratifying by time point.
- Direct CLR rank summaries: Kruskal or Wilcoxon within time points, paired Wilcoxon within subjects when paired IDs are available.

Keep these formulas synchronized across model specification, scripts, result objects, and report method text.
