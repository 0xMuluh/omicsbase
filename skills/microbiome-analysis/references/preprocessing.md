# Preprocessing

Use this reference when changing microbiome preprocessing code.

## Filtering

Prevalence and abundance filters affect every downstream result. Record:

- prevalence threshold
- abundance threshold
- sample subset used to compute filtering
- taxonomic rank before or after aggregation
- number of features retained and removed

Do not apply filtering separately inside each model unless the analysis plan requires model-specific feature sets.

## Zero Handling

Zero values may represent true absence, under-sampling, or detection limits. Check the method before adding pseudo-counts. Pseudo-count rules must be documented and applied consistently.

## Normalization And Transformation

Common approaches include:

- total-sum scaling or relative abundance
- centered log-ratio after zero handling
- variance-stabilizing transformations
- rarefaction for selected diversity analyses

Avoid treating rarefaction as a default normalization for all inferential analyses. If rarefaction is used, document why and preserve random seeds.

## Metadata Joins

Join metadata after confirming sample ID uniqueness. Do not drop samples silently. Summarize dropped samples and missing metadata.
