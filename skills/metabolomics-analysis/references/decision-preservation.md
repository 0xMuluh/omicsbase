# Decision Preservation

Use this reference before changing analysis code, Quarto report code, tables, figures, configs, or result-object contracts.

## Core Rule

Treat every material choice already present in the project as an analysis decision until proven otherwise. Do not overwrite, simplify, rename, or refactor it away without understanding what decision it encodes.

## Decisions To Preserve

Capture and preserve decisions from:

- analysis scripts and helper functions
- Quarto setup chunks and page-specific chunks
- model formulas and contrast definitions
- covariate blocks and sensitivity covariates
- exclusion and filtering rules
- complete-case rules and minimum sample thresholds
- visit, time-window, and sample-linkage mappings
- feature transforms, scaling, aggregation, and filtering
- factor casting, reference levels, and label mappings
- multiple-testing correction method and correction family
- result-object component names and column contracts
- table builders, column labels, sort orders, and display filters
- figure builders, axes, facets, thresholds, and captions
- report wording that constrains interpretation
- render configuration and navigation

## Working Procedure

1. Inventory relevant decisions before editing. Search local scripts, QMDs, READMEs, TODOs, configs, and result consumers.
2. Decide whether each decision is being preserved, corrected, generalized, or intentionally replaced.
3. Make the smallest source change that carries the decision forward.
4. Update all dependent tables, figures, report text, validation checks, and result-object contracts when a decision changes.
5. In the final response, state which material decisions were preserved and which changed.

## Stop Conditions

Stop and ask when a decision is scientifically material and cannot be inferred from local artifacts, including covariate membership, exposure/outcome roles, exclusion rules, FDR scope, primary-vs-sensitivity status, visit mapping, or interpretation wording.
