---
name: static-report-publishing
description: Use when packaging, validating, indexing, releasing, or publishing static research reports, Quarto HTML output bundles, report portal metadata, reports.json entries, WordPress analysis-report plugin uploads, protected report routes, latest-version routing, or release checklists for hosted analysis reports.
---

# Static Report Publishing

Use this skill to prepare static analysis reports for safe publication or protected collaborator sharing.

## Operating Rules

1. Treat rendered report directories as release artifacts. Package them only after source analysis/report changes have been rendered or explicitly accepted.
2. Validate bundle structure before upload. A release bundle must contain `index.html` at the root and must not contain executable server-side files.
3. Keep hosting concerns separate from analysis logic. Do not modify statistical source code while doing a packaging-only task.
4. Update metadata registries such as `reports.json` deliberately and validate required fields, unique keys, dates, links, visibility, and draft state.
5. Preserve protected/private status unless the user explicitly asks to make a report public.

## Workflow

1. Identify the report output directory and confirm it has a root `index.html`.
2. Validate the output directory or ZIP bundle with `scripts/validate_bundle.sh`.
3. If portal metadata changes, validate `reports.json` with `scripts/validate_reports_json.py`.
4. Package using the local project packaging script when one exists.
5. For WordPress upload flows, verify analysis key, version, language, latest flag, and access behavior.
6. Report the exact bundle path, version/date tag, and validation status.

## Scripts

Use `scripts/validate_bundle.sh <output-dir-or-zip>` to check static bundle safety.

Use `scripts/validate_reports_json.py <reports.json>` to check report portal metadata.

## References

Read `references/bundle-contract.md` when validating static report output.

Read `references/reports-json-schema.md` when adding or editing portal metadata.

Read `references/wordpress-plugin-flow.md` when preparing a WordPress plugin upload.

Read `references/release-checklist.md` before claiming a report release is ready.
