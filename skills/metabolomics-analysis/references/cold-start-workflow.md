# Cold-Start Workflow

Use this reference when starting a metabolomics analysis from scratch or when receiving an unfamiliar project with no reliable README.

## Intake Questions

Ask or infer only what is needed to begin safely:

- What is the scientific question?
- What are the exposure variables, outcome variables, and covariates?
- Which file contains clinical/sample metadata?
- Which file contains metabolite features?
- What is the subject identifier?
- How are visits or time windows encoded?
- Are repeated measures, paired samples, or longitudinal models required?
- What is the primary model family and what are sensitivity models?
- What FDR threshold and correction family should be used?

Do not invent covariates, exclusions, visits, or model families when these are scientifically material.

## Minimum Project Inputs

A cold-start metabolomics project needs:

- sample metadata with one row per subject/sample or subject-visit
- metabolite feature table or assay object
- data dictionary or variable key if names are not self-explanatory
- analysis question and model specification
- report destination or preferred Quarto structure

## Build Sequence

1. Create or locate a study configuration file. For an empty project, run `scripts/create_metabolomics_project.py` and adapt the generated `config/analysis_plan.R`, `config/model_specification.csv`, and `config/decision_log.tsv`. Use `assets/metabolomics_study_config.yml` only when a YAML-style config is required by an existing project.
2. Inspect identifiers, visits, feature orientation, missingness, and duplicate samples.
3. Create a derived analysis object only after raw inputs and mappings are clear.
4. Implement preprocessing: log transform or pseudo-count rule, within-visit scaling if needed, feature filtering, and covariate typing.
5. Implement primary models and model-status logging before report pages.
6. Implement sensitivity models and result comparison only after primary models are stable.
7. Use `quarto-research-report` to scaffold or update the report site.
8. Validate result object contracts before rendering result pages.

## Cold-Start Output Contract

A mature project should produce:

- reproducible analysis scripts generated from the skill template or an equivalent project-local implementation
- saved result object(s) following `references/result-object-contract.md`
- model status log
- Quarto report source
- rendered report output
- project notes or report text documenting render commands and data assumptions

## Stop Conditions

Stop and ask for clarification when:

- exposure/outcome roles are ambiguous
- covariates are unknown or clinically material
- repeated-measures structure is unclear
- data files disagree about sample IDs or visits
- the requested interpretation requires causal language not supported by the design
