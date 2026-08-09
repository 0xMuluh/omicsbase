# Workspace Map

Use this reference to orient inside a generic multi-project research workspace.

## Common Directory Roles

Study project folders often contain:

- `code/`: Quarto, R, Python, or analysis source files
- `data/`: local raw or derived data, often private
- `output/`: rendered reports and generated results
- `results/`: saved model outputs, tables, or figures
- `refs/` or `references/`: study plans, manuscripts, data dictionaries, methods notes
- `scripts/`: shared or project-local helper scripts
- `assets/`: report or portal assets

Publishing folders may contain:

- static report portals
- `reports.json` or similar metadata registries
- WordPress/plugin code
- deploy scripts
- packaged ZIP bundles

## Classification Rules

Do not assume every top-level folder is a software repository. Classify each folder by evidence:

- Git repository: contains `.git` and meaningful source files
- Quarto site: contains `_quarto.yml` and `.qmd` files
- analysis project: contains data, scripts, and report outputs
- publishing project: contains portal metadata, plugin code, or bundles
- reference folder: contains plans, PDFs, DOCX, methods notes
- local artifact folder: contains generated outputs, caches, archives, or session files

## Reporting Orientation

When reporting workspace findings, separate:

- source projects
- generated outputs
- private/local data
- publishing infrastructure
- active user work
- unresolved risks or questions
