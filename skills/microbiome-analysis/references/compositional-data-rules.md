# Compositional Data Rules

Use this reference when interpreting microbiome relative abundances or log-ratio outputs.

## Core Principle

Microbiome sequencing profiles are usually compositional. A change in one taxon can alter relative abundances of other taxa even when their absolute abundances do not change.

## Interpretation Rules

- Say "relative abundance" unless absolute abundance data are available.
- Avoid causal language from cross-sectional association models.
- Avoid interpreting relative abundance coefficients as direct changes in organism load.
- State the transformed scale when using CLR, ALR, arcsine, log-relative abundance, or rank-based outputs.
- Treat zeros and rare taxa as method-sensitive.

## Reporting

When reporting differential abundance, include enough context for the reader to understand the scale, contrast, and taxonomic level. For example:

`The model tested genus-level CLR-transformed abundance adjusted for intervention group and baseline covariates.`
