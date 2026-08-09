# Package Bootstrap

Use this reference before running, installing, or updating R packages for a microbiome workflow. Package handling is part of reproducibility, not an afterthought.

## Required Behavior

1. Read the project package manifest, usually `config/r_package_manifest.csv`. If it is absent, use `assets/r_package_manifest.csv` from this skill.
2. Run `scripts/bootstrap_r_packages.R --manifest <manifest.csv> --output <status.tsv>` before analysis.
3. Record package name, source, priority, installed version, and status in the project results.
4. If packages are missing, ask before installing. Use `--install` only with explicit approval or when the execution environment policy already allows installs.
5. After installing or updating packages, rerun `scripts/check_r_package_contract.R` to verify function availability and signatures.
6. Never switch methods because a package is missing. Write a status row and stop that branch unless the user changes the analysis plan.

## Package Sources

- `cran`: install with `install.packages()`.
- `bioc`: install with `BiocManager::install()`.
- `base`: provided with R; check version only.
- `optional`: install/check only when the analysis plan requests the method.

## Reproducibility Output

A project should keep these files when practical:

- `results/r_package_status.tsv`: package installation/check status.
- `results/package_function_contract.tsv`: function availability and formal arguments.
- `config/r_package_manifest.csv`: the package manifest used for the run.
- `config/decision_log.tsv`: package installation or method-unavailable decisions.
