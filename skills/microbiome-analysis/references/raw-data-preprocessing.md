# Raw Data Preprocessing

Use this reference before converting raw microbiome inputs into analysis-ready objects. The skill should start from raw data plus an analysis plan, reduce basic human decisions, and stop only when the plan is missing a scientific decision that affects validity.

## Supported Input Kinds

| `preprocessing$input_kind` | Expected input | Default action |
| --- | --- | --- |
| `feature_table` | CSV/TSV with samples as rows or features as rows | Reshape to sample-by-feature matrix, align metadata, filter features. |
| `metaphlan_profile` | MetaPhlAn merged profile table with clades/features as rows and samples as columns | Parse feature-by-sample table, optionally filter terminal taxonomic rank, clean sample names, align metadata. |
| `humann_profile` | HUMAnN/HUMAnN3 pathway/gene-family profile with features as rows and samples as columns | Parse feature-by-sample table, preserve feature family, align metadata, filter features. |
| `tree_summarized_experiment_rds` | Existing `TreeSummarizedExperiment`/`SummarizedExperiment` RDS | Extract configured assay and `colData`; preserve object in the derived result. |
| `phyloseq_rds` | Existing `phyloseq` RDS | Extract OTU table and sample data after checking package availability. |

If the user provides FASTQ/BAM-level sequencing files, do not pretend this downstream skill can profile reads by itself. Create an upstream profiling command plan only when the analysis plan specifies the profiler, database, version, and compute environment; otherwise ask for processed profiler outputs.

## Required Preprocessing Decisions

Record these in `analysis_plan.R`, `model_specification.csv`, result objects, or `decision_log.tsv`:

- input kind and parser
- sample ID column and sample-name cleanup rule
- metadata uniqueness and feature-table uniqueness checks
- feature orientation
- input scale: counts, relative abundance, percentage, CLR, or method-specific
- taxonomic rank or feature family
- filtering thresholds and whether filtering happens before or after rank aggregation
- zero handling and pseudo-count
- sample exclusion/missing metadata policy
- generated derived object path

## Stop Conditions

Stop and ask the human when:

- sample IDs cannot be aligned after declared cleanup rules
- a feature/profile table has ambiguous orientation and the plan does not specify it
- input scale is unknown and affects model choice or interpretation
- taxonomic rank or feature family cannot be inferred and affects the planned analysis
- the plan requests an upstream read-profiling step without profiler/database/container details

## Derived Object Contract

`code/01_prepare_data.R` should create `derived/microbiome_analysis_data.rds` containing at minimum:

- `plan`
- `metadata` aligned to the feature matrix
- `feature_matrix` as samples x features
- `feature_map` with original feature names, safe column names, retention, prevalence, and abundance
- `sample_alignment` with metadata-only and feature-only sample summaries
- `sample_summary` and `feature_summary`
- `preprocessing_summary`
- optional original object, such as `tse` or `phyloseq`, when imported
