# Data Contract

Use this reference when mapping metabolomics inputs or checking whether an analysis script and report page agree about the same data structure.

## Expected Study Layout

A typical study folder contains:

- `code/`: Quarto source files, analysis scripts, helper scripts, and `_quarto.yml`
- `data/` or `data_v2/`: local raw or derived study data
- `output/`: rendered Quarto site and saved model results
- `output/results/`: `.rds` model result objects consumed by report pages

Treat `data/`, `data_v2/`, `.rds`, `.sav`, `.xlsx`, `.RData`, and `.Rhistory` as local study state unless the user asks to rebuild or inspect them.

## Identifiers And Visits

Confirm the subject identifier and visit encoding before changing joins or model code.

Common patterns:

- `StudyID`: participant or child identifier
- `visit_num`: numeric visit, often extracted from a study visit label
- `Visit`: factor-like visit label, often `visit_4`, `visit_5`, `visit_6`, `visit_7`
- `visit_factor`: repeated-measures factor such as `6` and `7`

When longitudinal outputs use a generic exposure column such as `exposure_it`, preserve the original exposure family with a companion field such as `exposure_pair`, `exposure_v6`, or `exposure_v7`.

## Variable Mapping Schema (Schema Mapping Layer)

To guarantee reproducibility across datasets, the project must have a declarative mapping file (e.g., `schema_map.yml` or `study_config.yml`) that acts as the source of truth for column names, data types, and coding schemas. Do not dynamically guess columns.

The map must define:
- Subject ID column name
- Time/Visit column name and visit numerical codes
- Exposure and outcome variables and their categories
- Categorical covariate reference levels (e.g., mode of delivery reference level)
- Human-readable outcome and exposure labels for the final Quarto reports

## Assays And Features

Confirm assay name and orientation before fitting or summarizing models.

Typical metabolomics code expects:

- metabolite features as assay rows and samples as columns in `SummarizedExperiment`-like objects
- `mbo` as the metabolite assay name in some existing project scripts
- feature-level outputs with `feature` or `outcome` naming
- ratio features identified by a pattern such as `-ratio`

Do not assume all features are primary metabolites. Check whether the analysis separates metabolite and ratio tracks.

## Covariates

Resolve planned covariates against available data before fitting models. Keep both present and missing covariates in model status logs where possible.

Common covariate classes:

- core confounders: sex, birth weight, maternal age, maternal BMI, education, primiparity, gestational diabetes, smoking, breastfeeding duration, intervention arm
- measurement covariates: child fasting hours, medication, antibiotics, health status at blood draw
- sensitivity covariates: prematurity, SGA, macrosomia, mode of delivery, Apgar variables

Do not silently drop covariates without logging or reporting the missing variables.
