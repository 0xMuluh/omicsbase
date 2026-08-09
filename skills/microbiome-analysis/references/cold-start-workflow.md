# Cold-Start Workflow

Use this reference when starting a microbiome downstream analysis from scratch or receiving unfamiliar microbiome project files.

## Intake Questions

Ask or infer only what is needed to begin safely:

- What sequencing or profiling method produced the data?
- Are inputs counts, relative abundances, MetaPhlAn profiles, ASV/OTU tables, or an existing object?
- What is the sample identifier and where is sample metadata?
- What taxonomic rank should be analyzed?
- What groups, exposures, outcomes, and covariates are planned?
- Are samples paired, longitudinal, repeated, or independent?
- Which analysis families are required: alpha diversity, beta diversity, PERMANOVA, differential abundance?
- Which transformation or compositional method is expected?

## Minimum Project Inputs

A cold-start microbiome project needs:

- count/profile table or microbiome object
- sample metadata
- taxonomy table or parseable taxonomic names
- grouping/exposure variable definitions
- model/covariate plan
- report destination or preferred Quarto structure

## Build Sequence

1. Create or locate a study configuration file. For an empty project, run `scripts/create_microbiome_project.py` and adapt the generated `config/analysis_plan.R`, `config/model_specification.csv`, and `config/decision_log.tsv`. Use `assets/microbiome_study_config.yml` only when a YAML-style config is required by an existing project.
2. Inspect sample IDs, duplicate samples, feature orientation, taxonomy rank structure, and missing metadata.
3. Build or load a microbiome object only after sample and feature mappings are clear.
4. Apply filtering and transformation rules with retained-feature summaries.
5. Implement alpha and beta diversity analyses if requested.
6. Implement differential abundance with compositional assumptions documented.
7. Use the bundled `report/code` Quarto scaffold to generate or update the report site and connect QMD pages to the config, result objects, and decision log.
8. Validate output tables, ordination labels, and interpretation scale.

## Stop Conditions

Stop and ask for clarification when:

- sample IDs do not align between feature table and metadata
- taxonomic rank or feature orientation is ambiguous
- paired or repeated-measures structure is unclear
- requested interpretation treats relative abundance as absolute abundance
- method choice is material and not specified
