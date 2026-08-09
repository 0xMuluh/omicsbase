# Scenario Decision Map

Use this reference before generating or changing microbiome downstream analysis code. Select the matching branch, then encode the choice in `config/model_specification.csv`, scripts, result objects, and QMD text.

## Universal Defaults

- Align samples before filtering or modeling; never drop samples silently.
- Record input scale as counts, relative abundance, CLR, or model-specific scale before interpreting effects.
- Run the package/function contract check before package-backed methods.
- Use prevalence and abundance filters from the plan; if absent, create a candidate config value and require confirmation before inferential modeling.
- Use Benjamini-Hochberg FDR unless the plan specifies another method; record the correction family explicitly.
- Preserve compositional limitations in method text and result interpretation.
- Write a status row for every skipped, missing, changed-signature, unavailable, or failed method branch.

## Scenario Branches

| Trigger in draft plan or data | Generated analysis family | Required generated decisions | Script behavior | Report/QMD output |
| --- | --- | --- | --- | --- |
| Raw feature table and metadata, no downstream question yet | `microbiome_inventory` | sample ID, feature orientation, input scale, taxonomy source, rank, missing metadata | Generate config, inspect alignment, summarize samples/features, stop before inferential claims | Study overview and data summary only |
| MetaPhlAn profile table | `metaphlan_tse_preprocessing` | parser, sample cleanup rule, metadata join key, relative abundance assay name | Prefer `mia::importMetaPhlAn`, attach `colData`, transform to relative abundance, add alpha metrics, create rank alt experiments | Input summary, feature/rank summary, decision log |
| HUMAnN3 path abundance or MetaCyc-like profile | `functional_profile_preprocessing` | parser, feature family, sample alignment, altExp name | Add as `SummarizedExperiment` alt experiment; transform to relative abundance | Functional feature summary |
| Compare richness/diversity by group or exposure | `alpha_diversity` | metrics, group/exposure, visit/time, subject ID, test family | Use Wilcoxon/FDR tables; add LMM branch when repeated measures exist | Alpha metric summary, group table, LMM/post-hoc results |
| Community composition differs by group/exposure | `beta_diversity_permanova` | distance metric, transformation, formula, permutations, strata/blocking, seed | Use Bray PCoA and PERMANOVA when package/function checks pass; otherwise status row | Ordination plot/table, PERMANOVA table, variance explained, homogeneity note |
| Feature-level taxon associations | `differential_abundance` | CLR pseudo-count, rank, MaAsLin3 formula, random effect, FDR family | Run CLR rank summaries and MaAsLin3 when package/function checks pass | DA result table, significant-hit plots, effect scale note |
| Functional differential abundance | `functional_differential_abundance` | feature family, MaAsLin3 formula, output directory, enzyme/pathway labels | Run MaAsLin3 on functional alt experiments when available | Functional DA tables and highlighted pathway plots |
| Longitudinal or paired microbiome samples | `longitudinal_microbiome` | subject ID, visit/time variable, random effect or paired test, within-person contrasts | Use mixed model, paired Wilcoxon, or blocked/permutation branch as appropriate | Visit availability, within-person model/status table |
| Sensitivity method explicitly requested | `sensitivity_microbiome` | method, package version, function signature, alternate filter/rank/transform | Run optional ANCOM-BC/ALDEx2/DESeq2 only after checks and label as sensitivity | Estimate-shift, sign-change, and FDR reclassification table |

## Deterministic Fallbacks

If a requested method requires a package that is not installed or whose function signature changed, generate guarded code that reports the missing or changed contract and stops for that branch. Do not silently replace MaAsLin3, PERMANOVA, mixed models, ANCOM-BC, or ALDEx2 with unrelated tests.

If the draft plan conflicts with raw data structure, preserve both facts in the decision log and stop at the earliest script that proves the conflict.
