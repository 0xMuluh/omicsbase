---
name: research-workspace-routing
description: Use when navigating or modifying a multi-project research workspace, choosing between study folders, shared scripts, report portals, publishing tools, metabolomics workflows, microbiome workflows, Quarto report maintenance, static report publishing, or project-specific TODO and safety checks.
---

# Research Workspace Routing

Use this skill as a routing and safety layer for multi-project research workspaces.

## Operating Rules

1. Treat a workspace root as a container until proven otherwise. Identify the actual project folder before editing or running Git commands.
2. Read local `README.md`, project notes, and directory structure before assuming ownership boundaries.
3. Treat user-active worktrees as user-owned state. Do not clean, revert, reset, or overwrite current work unless explicitly asked.
4. Preserve raw data, credentials, `.RData`, `.Rhistory`, rendered outputs, and large archives unless the user explicitly asks to move or change them.
5. Choose the relevant domain skill before editing: metabolomics, microbiome, Quarto report, or static publishing.
6. Prefer source edits over generated-output edits.

## Routing

Use `metabolomics-analysis` for metabolomics modeling, result objects, covariates, visit mappings, and model reports.

Use `microbiome-analysis` for microbiome downstream analysis, taxonomic profiles, diversity, ordination, PERMANOVA, and differential abundance.

Use `quarto-research-report` for report prose, Quarto pages, tables, figures, navigation, skeleton creation, and rendering.

Use `static-report-publishing` for bundled HTML reports, portal metadata, upload preparation, protected routes, and release validation.

## Cold-Start Workspace Pass

1. List top-level directories and identify project candidates.
2. Locate `README.md`, `_quarto.yml`, analysis scripts, package manifests, and report outputs.
3. Determine whether each project is analysis source, rendered output, shared reference material, or publishing infrastructure.
4. Identify active Git repositories only inside project folders, not by assuming the workspace root is the main repository.
5. Summarize likely project boundaries and ask before broad cleanup, restructuring, or destructive operations.

## References

Read `references/workspace-map.md` to classify common research-workspace directories.

Read `references/safety-rules.md` before running broad commands or modifying generated/local/private files.
