# Pack Manifest

## Purpose

Provide portable agent skills for metabolomics, microbiome, and scientific reporting workflows. The pack must be useful when copied to another machine or handed to a fresh agent with no knowledge of the source conversation.

The pack assumes the user may provide only raw data plus a draft analysis plan. In that cold-start case, the domain skills must guide the agent to create the project-local config, model specification, scripts, QMD report source, result object contract, validation checks, and decision log.

## Included Skills

| Skill | Purpose | Cold-start capable |
| --- | --- | --- |
| `research-workspace-routing` | Route and protect work in a multi-project research workspace | Yes |
| `metabolomics-analysis` | Build, inspect, and validate metabolomics analyses | Yes |
| `microbiome-analysis` | Build, inspect, and validate microbiome analyses | Yes |
| `quarto-research-report` | Scaffold and maintain scientific Quarto report sites, including full metabolomics result-stage reports | Yes |
| `static-report-publishing` | Validate and publish static report bundles | Yes |

## Portability Rules

- Do not assume any specific local workspace exists.
- Do not assume raw data are present.
- Do not assume package dependencies are installed.
- Prefer project-local helpers when they exist.
- Use bundled references and templates when starting from an empty project.
- Ask for missing scientific decisions before inventing model assumptions.
- Treat every table layout, model formula, covariate block, filter, transform, QMD chunk, caption, and result-object field as a preserved decision.
