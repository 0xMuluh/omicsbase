# Writing Style

Use this reference when editing report prose, headings, comments, captions, READMEs, and analysis-plan text.

## Reference Implementations

The FOPP sites in `templates/metabolomics/` are the house voice in practice: read `prenatal_diet_metabolomics/code/design/study_overview.qmd` and `primary_results.qmd` for the tone, heading rhythm, and caption style this reference describes.

## Core Rule

Write like a careful analyst. Every sentence, heading, table, and comment should help the reader understand the study, data, model, result, or decision.

## Avoid

- chatbot or marketing language
- apologies, enthusiasm, filler, and broad claims not supported by the analysis
- meta-commentary about the workflow unless scientifically relevant
- vanity naming or analyst-name labels unless required for provenance
- comments that merely restate the next line of code
- old copied study names, TODO/FIXME placeholders, and stale caveats

## Prefer

- sentence-style headings
- short methods paragraphs
- table-driven specification for covariates, panels, visits, and model families
- concrete variable names when they clarify the analysis
- comments that preserve non-obvious decisions, coding assumptions, or data caveats

## Examples

Bad:

```markdown
This comprehensive and robust dashboard helps users explore fascinating metabolomics insights.
```

Better:

```markdown
This report summarizes exposure-metabolite associations, complete-case counts, and sensitivity-model stability across the planned child-age panels.
```

Bad:

```r
# Load data
df <- readRDS("data/MAE.rds")
```

Better:

```r
# Visit 7 fasting hours are retained as a measurement-condition covariate for the longitudinal panel.
df <- readRDS("data/MAE.rds")
```
