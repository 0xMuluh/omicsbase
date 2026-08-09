# Data Contract

Use this reference when mapping microbiome input files, object structure, and report outputs.

## Common Inputs

Microbiome projects commonly include:

- taxonomic profile tables from MetaPhlAn or similar tools
- ASV/OTU/count tables
- relative abundance tables
- taxonomy tables with rank annotations
- sample metadata with clinical, intervention, diet, or batch variables
- phyloseq or TreeSummarizedExperiment objects
- Quarto source and rendered reports

## Invariants

Check these before modeling:

- sample IDs match across count table and metadata
- taxa/features are rows or columns as expected by the analysis function
- taxonomy ranks are consistently encoded
- missing samples and duplicate sample IDs are handled explicitly
- feature names remain stable through filtering and aggregation
- grouping variables and covariates are present and typed correctly

## Variable Mapping Schema (Schema Mapping Layer)

To guarantee reproducibility across datasets, every project must have a declarative mapping file (e.g., `schema_map.yml` or `study_config.yml`) that acts as the source of truth for column names, data types, and coding schemas. Do not dynamically guess columns.

The map must define:
- Subject and sample ID column names
- Grouping/visit columns and taxonomy identifiers
- Taxonomic rank settings and aggregation parameters
- Categorical covariate reference levels (e.g., mode of delivery reference level)
- Human-readable taxon and exposure labels for Quarto reports

## Feature Orientation

Do not assume feature orientation. Inspect dimensions and names. Many ecology functions expect taxa as rows, but some project tables store taxa as columns after joining with metadata.

## Taxonomy

When collapsing ranks, document the selected rank and how unclassified taxa are handled. Preserve original feature IDs when possible so downstream diagnostics can trace results back to input data.
