# Validation Checklist

Use this checklist before reporting that a Quarto research-report change is complete.

## Source Checks

- Confirm edits were made in source files rather than only rendered output.
- Confirm page title, navbar label, and render list remain consistent.
- Confirm loaded result objects and helper functions still exist.
- Confirm changed labels match actual model/result fields.

## Text Checks

Scan for:

- TODO/FIXME placeholders
- copied names from another study
- chatbot or marketing phrasing
- unsupported causal claims
- stale caveats about current work state
- table headers with raw underscores where reader-facing labels are expected

## Render Checks

Render the affected page or full site when practical.

If rendering fails, preserve the relevant error and report the blocking file, chunk, or dependency. If rendering is not run, state that explicitly.

## Review Checks

Open or inspect the rendered page when possible. Confirm tables, figure captions, navigation, and generated assets align with the source changes.
