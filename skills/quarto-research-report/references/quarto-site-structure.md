# Quarto Site Structure

Use this reference when adding pages, changing navigation, moving files, or altering render behavior.

## Source And Output Split

A scientific Quarto site should keep source and rendered output separate.

Typical layout:

- `code/`: `.qmd`, `.R`, `_quarto.yml`, local report helpers
- `output/`: rendered HTML, static assets, result pages
- `output/results/`: saved model objects consumed by pages
- `data/`: local input data, usually not suitable for publishing

Edit source files first. Rendered HTML should be regenerated, not hand-edited, unless there is no source available.

## Navigation

When adding or renaming pages:

1. Update `_quarto.yml` render list.
2. Update `website.navbar` entries.
3. Confirm the rendered filename and link text are consistent.
4. Keep page titles short and study-neutral unless a study-specific title is scientifically necessary.

## Rendering

Prefer targeted rendering when possible during iterative edits. Use full-site rendering when shared setup, navigation, styles, or common helper logic changes.

Before rendering, check whether the page requires local data, saved model results, or external resources that may not exist in the current environment.

## External Scripts

External scripts in report headers should be intentional. For private or protected reports, confirm whether third-party embeds are appropriate before adding or preserving them.
