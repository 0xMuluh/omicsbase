---
name: quarto-research-report
description: Use when creating, editing, reviewing, or rendering scientific Quarto reports, Quarto websites, analysis-plan pages, covariate diagnostics, clinical characteristics, data summaries, data-quality assumptions, primary result tables, figures, supplementary tables, secondary figures, exposure atlases, report READMEs, generated QMD skeletons, or research-report prose for metabolomics, microbiome, and other data-science analysis projects.
---

# Quarto Research Report

Use this skill to maintain scientific Quarto reports that are clear, compact, reproducible, and aligned with the underlying analysis.

## Operating Rules

1. Edit source `.qmd`, `.R`, YAML, or data metadata rather than rendered HTML when a source exists.
2. Keep the report tone analytical. Avoid promotional copy, chatbot phrasing, apologies, motivational filler, and meta-commentary about the work process.
3. Treat QMD chunks, table builders, figure builders, labels, captions, and report prose as decision-bearing source. Preserve or explicitly update those decisions.
4. When no report exists, generate source QMDs from the report skeleton and adapt them to the domain skill's config, model spec, result object, and decision log.
5. Preserve real provenance: coding assumptions, visit mappings, duplicate handling, derived-variable rules, model decisions, and known data limitations.
6. Prefer compact scientific structure: short headings, direct explanations, and tables where tables communicate better than prose.
7. Make rendered tables readable. Use human-readable column labels and avoid exposing raw implementation names unless they are scientifically necessary.
8. Keep visual and table changes tied to a scientific question, model result, or data-quality concern.

## Workflow

1. Read the study `README.md`, `_quarto.yml`, and affected `.qmd` page before editing.
2. Locate setup chunks, shared helper functions, loaded result objects, and page-specific table/figure builders.
3. Inventory decisions encoded in tables, figures, labels, captions, headings, filters, thresholds, and narrative interpretation.
4. If creating a report from raw data and a draft plan, read `references/generated-report-contract.md` and create the required QMD pages before polishing prose.
5. Make source edits narrowly, preserving established naming, navigation, styling, and render options.
6. Scan edited text for stale study names, TODO/FIXME markers, unsupported claims, and AI-sounding filler.
7. Render the affected page or site when practical. If rendering is not practical, explain what was not verified.

## Scripts

Use `scripts/create_quarto_report_skeleton.py <target-dir> --title "Study title"` to create a minimal scientific Quarto site from the bundled template. Add `--template metabolomics-full` when the report should include full metabolomics result-site pages.

Use `scripts/scan_report_text.R <file-or-dir>...` to check report prose and source comments for common style issues.

## References

Read `references/new-report-workflow.md` when creating a Quarto report from scratch.

Read `references/generated-report-contract.md` when generating QMD pages from a draft analysis plan or connecting a domain skill's generated scripts to report pages.

Read `references/metabolomics-report-architecture.md` when generating or repairing a full metabolomics report site with primary results, figures, supplementary tables, secondary figures, exposure atlas, clinical characteristics, data summary, and data-quality assumptions.

Read `references/decision-preservation.md` before changing QMD chunks, tables, figures, labels, captions, prose, YAML, or render behavior.

Read `references/writing-style.md` when changing report prose, headings, captions, comments, README text, or analysis-plan text.

Read `references/quarto-site-structure.md` when adding pages, moving pages, editing navigation, or changing render behavior.

Read `references/table-figure-standards.md` when changing tables, plots, captions, labels, or result summaries.

Read `references/validation-checklist.md` before reporting completion.
