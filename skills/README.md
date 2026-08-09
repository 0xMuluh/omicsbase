# Analysis Skill Pack

This directory is a standalone skill pack for agents working on metabolomics, microbiome downstream analysis, scientific Quarto reporting, static report publishing, and generic multi-project research workspaces.

Give this directory to an agent as the skill source. Each child directory with a `SKILL.md` is an independent skill. The skills are designed to work without prior conversation context: the user can provide raw data plus a draft analysis plan, and the agent should create the project config, scripts, model specifications, Quarto source, result contracts, and validation checks needed to complete the analysis.

## Skills

- `research-workspace-routing`: route work safely in a multi-project research workspace.
- `metabolomics-analysis`: generate, inspect, modify, and validate metabolomics workflows.
- `microbiome-analysis`: generate, inspect, modify, and validate microbiome downstream workflows.
- `quarto-research-report`: create and maintain scientific Quarto report sites, including full metabolomics result-stage reports.
- `static-report-publishing`: validate, package, index, and release static reports.

## Cold-Start Order

For a new analysis project, use this order:

1. Use `research-workspace-routing` if the workspace contains multiple projects, generated outputs, private data, or publishing infrastructure.
2. Use `metabolomics-analysis` or `microbiome-analysis` to turn raw inputs and the draft plan into a project-local config, model specification, analysis scripts, result object contract, and validation sequence.
3. Use `quarto-research-report` to generate or maintain the report site and QMD pages.
4. Use `static-report-publishing` only after rendered output is ready to release.

## Installation

Copy the skill directories into the agent skill location, such as `$CODEX_HOME/skills` or `~/.codex/skills`, or point the agent directly at this directory if the environment supports local skill loading.
