# Release Checklist

Use this checklist before reporting that a static report release is ready.

## Before Packaging

- Confirm source report changes were rendered.
- Confirm output directory contains `index.html`.
- Confirm private/protected reports are not being moved to public hosting unintentionally.
- Confirm no raw data files are included unless explicitly intended.

## Bundle Validation

- Run `scripts/validate_bundle.sh <output-dir-or-zip>`.
- Confirm no executable server-side files are present.
- Confirm ZIP paths are safe if packaging as ZIP.

## Portal Metadata

- Run `scripts/validate_reports_json.py <reports.json>` if metadata changed.
- Confirm `key`, `updated`, `visibility`, `reportUrl`, and `draft` state.

## Final Report

Report:

- bundle path
- version/date tag
- report key
- validation commands run
- anything not verified
