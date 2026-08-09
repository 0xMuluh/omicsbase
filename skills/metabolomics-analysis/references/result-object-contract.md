# Result Object Contract

Use this reference before editing report pages that consume saved `.rds` result objects.

## Inspection First

Before changing report logic, inspect the object names and columns with targeted R code. Confirm whether the object is a list and which components are consumed by the page.

Common components:

- age-specific or visit-specific results
- concurrent, prospective, repeated-measures, and sensitivity result tables
- `model_status` or execution logs
- feature metadata or cluster metadata

## Common Column Roles

Feature-level result tables usually need:

- `feature` or `outcome`: metabolite feature identifier
- `exposure`: exposure variable or generic model exposure
- `exposure_pair`: base exposure family for paired or repeated models
- `term`: tested coefficient or factor-level term
- `estimate`: standardized effect estimate
- `std.error`, `conf.low`, `conf.high`: uncertainty fields
- `p.value`: nominal p-value
- `q.value`: FDR-adjusted p-value
- `n` or `n_obs`: complete-case sample size
- `analysis`, `question`, `visit`, or `timing`: model family labels

If a report table needs pretty labels, derive them from stable source fields rather than overwriting source identifiers unless the existing study pattern already does that.

## Contract Changes

When changing a result object contract:

1. Update the analysis script that writes the object.
2. Update all report pages that consume the changed component.
3. Update validation checks or status summaries.
4. Rebuild the result object before rendering dependent pages.

Avoid patching report code to compensate for a malformed object unless rebuilding is impossible or the user explicitly wants a report-side compatibility layer.
