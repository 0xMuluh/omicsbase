# Safety Rules

Use this reference before broad workspace operations.

## Do Not Modify Without Explicit Request

- raw data in `data/`, `data_v2/`, or equivalent project data folders
- `.sav`, `.xlsx`, `.rds`, `.RData`, `.Rhistory`
- credential or access-control files such as `.htpasswd-*`, `.env`, keys, and tokens
- generated `output/`, cache, freeze, or rendered-site directories unless rendering/package tasks require it
- large archives such as `.zip` bundles
- user-active worktree changes

## Git Boundaries

A workspace root may be a container. Before using Git, identify the actual project folder and run status there.

Do not infer that a dirty worktree is a problem. It may be active user work.

## Editing Preference

Prefer source files:

- `.R`, `.py`, or other analysis scripts
- `.qmd`
- `_quarto.yml`
- study config files
- report portal metadata
- plugin source code when the user asks for publishing behavior changes

Avoid direct edits to rendered HTML or generated libraries unless no source exists or the user asks for a generated artifact patch.

## Validation Preference

Use the narrowest relevant validation:

- targeted script or result inspection for analysis logic
- page or site render for Quarto changes
- bundle validation for publishing
- metadata validation for report registries
