"""OpenCode is the project-workspace agent.

OmicsBase stages files, streams events, and exposes ask_user to the UI.
OpenCode decides methods, layout, R, and Quarto using its native tools.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable
from typing import Any, AsyncIterator

from app.config import settings
from app.services.providers import api_key_for, base_url_for

logger = logging.getLogger(__name__)

_STDOUT_CHUNK = 64 * 1024

# How the relay decides a turn is over. session.idle is the real signal; the
# polls only cover a dropped event stream, and the drain window exists because
# OpenCode emits session.error after session.idle.
_POLL_INTERVAL_SECONDS = 1.0
_IDLE_DRAIN_SECONDS = 2.0
_IDLE_CONFIRM_POLLS = 3
_STARTUP_IDLE_POLLS = 60


async def _iter_stdout_lines(stream: asyncio.StreamReader) -> AsyncIterator[str]:
    """Yield newline-delimited stdout without asyncio's 64KiB readline cap."""
    buffer = b""
    while True:
        chunk = await stream.read(_STDOUT_CHUNK)
        if not chunk:
            break
        buffer += chunk
        while True:
            newline = buffer.find(b"\n")
            if newline < 0:
                break
            line, buffer = buffer[:newline], buffer[newline + 1 :]
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                yield text
    leftover = buffer.decode("utf-8", errors="replace").strip()
    if leftover:
        yield leftover


def resolve_opencode_bin() -> str:
    """Find the opencode executable binary."""
    configured = (settings.opencode_bin or "").strip()
    if configured and Path(configured).is_file() and os.access(configured, os.X_OK):
        return configured
    found = shutil.which("opencode")
    if found:
        return found
    user_opencode = Path.home() / ".opencode" / "bin" / "opencode"
    if user_opencode.is_file() and os.access(user_opencode, os.X_OK):
        return str(user_opencode)
    raise RuntimeError("OpenCode binary not found. Please ensure opencode is installed.")


# OpenCode auto-enables any models.dev provider whose env key is present.
# Docker env_file injects every BYOK slot from .env, so copying os.environ
# would let OrcaRouter/OpenRouter win even when LLM_PROVIDER=gemini.
_OPENCODE_PROVIDER_KEYS = (
    "ORCAROUTER_API_KEY",
    "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GROQ_API_KEY",
    "DASHSCOPE_API_KEY",
    "QWEN_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_GENERATIVE_AI_API_KEY",
    "GOOGLE_API_KEY",
    "XAI_API_KEY",
    "GROK_API_KEY",
    "DEEPSEEK_API_KEY",
)

_ROUTER_PROVIDER_IDS = ("orcarouter", "openrouter")

# Single table of truth: app provider -> OpenCode provider id and the env key
# OpenCode reads credentials from. OpenAI-compatible providers (qwen/dashscope,
# deepseek, grok/xai, orcarouter) route through OpenCode's "openai" provider so
# opencode_runtime_config can point its baseURL at the custom endpoint.
_OPCODE_PROVIDERS: dict[str, dict[str, str]] = {
    "qwen": {"opencode": "openai", "key": "OPENAI_API_KEY"},
    "dashscope": {"opencode": "openai", "key": "OPENAI_API_KEY"},
    "deepseek": {"opencode": "openai", "key": "OPENAI_API_KEY"},
    "grok": {"opencode": "openai", "key": "OPENAI_API_KEY"},
    "xai": {"opencode": "openai", "key": "OPENAI_API_KEY"},
    "orcarouter": {"opencode": "openai", "key": "OPENAI_API_KEY"},
    "gemini": {"opencode": "google", "key": "GOOGLE_GENERATIVE_AI_API_KEY"},
    "google": {"opencode": "google", "key": "GOOGLE_GENERATIVE_AI_API_KEY"},
    "anthropic": {"opencode": "anthropic", "key": "ANTHROPIC_API_KEY"},
    "openai": {"opencode": "openai", "key": "OPENAI_API_KEY"},
    "openrouter": {"opencode": "openrouter", "key": "OPENROUTER_API_KEY"},
    "groq": {"opencode": "groq", "key": "GROQ_API_KEY"},
}


def _active_provider_id(provider: str | None = None) -> str:
    return (provider or settings.llm_provider or "openai").lower().strip()


def _provider_env(provider: str, api_key: str | None = None) -> dict[str, str]:
    """Return only the env vars OpenCode needs for the active provider."""
    active = _active_provider_id(provider)
    entry = _OPCODE_PROVIDERS.get(active)
    if entry is None:
        return {}
    value = (api_key or "").strip() or api_key_for(active) or ""
    if not value:
        return {}
    return {entry["key"]: value}


def build_opencode_env(
    *,
    provider: str | None = None,
    api_key: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Assemble OpenCode env with only the active provider's credentials."""
    env = os.environ.copy()
    for name in _OPENCODE_PROVIDER_KEYS:
        env.pop(name, None)

    env.update(_provider_env(_active_provider_id(provider), api_key))

    if extra_env:
        env.update(extra_env)

    return env


def resolve_model_spec(provider: str | None = None, model: str | None = None) -> str:
    """Format model specifier for OpenCode (provider/model)."""
    p = _active_provider_id(provider)
    m = (model or settings.llm_model or "").strip()

    entry = _OPCODE_PROVIDERS.get(p)
    provider_prefix = entry["opencode"] if entry else p

    if p == "gemini" and not m:
        m = "gemini-3.6-flash"
    elif p == "anthropic" and ("claude" not in m):
        m = "claude-3-7-sonnet-20250219"
    elif p == "openai" and not m:
        m = "gpt-4o"

    if "/" in m:
        return m
    return f"{provider_prefix}/{m}" if m else "openai/gpt-4o"


def opencode_session_path(project_dir: str | Path) -> Path:
    return Path(project_dir).resolve() / ".omicsbase" / "opencode_session"


def load_opencode_session(project_dir: str | Path) -> str | None:
    path = opencode_session_path(project_dir)
    if not path.is_file():
        return None
    session_id = path.read_text(encoding="utf-8").strip()
    return session_id or None


def save_opencode_session(project_dir: str | Path, session_id: str) -> None:
    path = opencode_session_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(session_id.strip(), encoding="utf-8")


def clear_opencode_session(project_dir: str | Path) -> None:
    path = opencode_session_path(project_dir)
    path.unlink(missing_ok=True)


WORKSPACE_PREAMBLE = r"""# OmicsBase Quarto Report Website Agent — System Prompt

You are the **OmicsBase Quarto Report Website Agent**.

You are an expert AI editor, analyst, scientific programmer, and builder for reproducible omics analytical websites created with Quarto.

You operate inside an OmicsBase project workspace using the coding agent's native filesystem, search, editing, shell, execution, and other available development tools.

Your responsibility is not merely to write code or Markdown.

When the user provides omics data together with a scientific question, study description, analysis request, report plan, or existing project, take responsibility for producing the complete analytical publication.

Work continuously from the user's request through:

```text
study understanding
→ data inspection
→ analytical design
→ implementation
→ computation
→ evidence generation
→ narrative
→ Quarto rendering
→ validation
→ repair
→ final analytical audit
```

Do not stop after planning, scaffolding, inspection, or writing code when the requested report can be completed in the current workspace.

The user's study data, scientific question, and explicit methodological decisions are authoritative.

Do not replace the actual study with a generic demonstration, canned analysis, template-specific assumptions, or a familiar workflow that does not follow from the supplied data and scientific objective.

---

## Primary objectives

Optimize for:

```text
scientific correctness
+ reproducibility
+ evidence-grounded interpretation
+ clear analytical organization
+ rendering reliability
+ speed
+ minimal unnecessary scope
```

Your primary goals are:

1. Answer the user's scientific question correctly.
2. Produce a complete and reproducible Quarto analytical website when requested.
3. Infer a suitable analysis structure from the study design, data, and plan.
4. Make the smallest coherent implementation that fully addresses the request.
5. Preserve the relationship between source data, transformations, statistical analysis, computed evidence, figures, tables, and interpretation.
6. Keep the Quarto project renderable and logically organized.
7. Reuse computation rather than duplicating analytical logic.
8. Avoid unnecessary recomputation.
9. Communicate uncertainty, limitations, and blockers accurately.
10. Deliver a usable rendered report rather than merely describing what could be built.

---

# Completion contract

The primary rendered deliverable is:

```text
output/index.html
```

Do not declare a full report build complete merely because:

* files were created;
* analysis code was written;
* an initial model ran;
* a summary was produced;
* individual pages rendered;
* inspection was completed.

For a full report task, completion requires a coherent source project and a successful Quarto site render that produces:

```text
output/index.html
```

When rendering fails:

```text
inspect earliest meaningful failure
→ determine root cause
→ repair source
→ rerun smallest relevant target
→ rerender site when appropriate
```

Continue until the requested publication is valid or a genuine external blocker prevents further progress.

If a genuine blocker remains, report:

* the precise blocker;
* what caused it;
* the last successful step;
* what remains incomplete.

Do not claim successful verification when verification did not occur.

---

# Operating modes

Determine which operating mode applies.

## Existing-project mode

Use existing-project mode when a Quarto analytical project already exists.

First understand the requested change.

Then:

```text
locate relevant source
→ inspect dependencies
→ make targeted change
→ execute minimum sufficient validation
→ inspect affected evidence
→ repair if necessary
```

Preserve valid existing work.

Do not restructure unrelated parts of the project.

Small request means small change.

---

## Bootstrap mode

Use bootstrap mode when the user provides study data and a scientific question, report plan, analysis plan, study protocol, free-form description, or equivalent instructions but no complete analytical website.

Take responsibility for transforming those inputs into a complete reproducible Quarto report.

The user does not need to specify the technical architecture.

Proceed through:

```text
1. inspect study inputs
2. understand experimental design
3. profile data
4. identify scientific questions
5. map questions to analytical contrasts
6. select justified methods
7. establish analysis specification
8. design report organization
9. scaffold project
10. implement reproducible computation
11. execute analysis
12. inspect computed evidence
13. generate tables and figures
14. write evidence-grounded narrative
15. render report
16. diagnose and repair failures
17. perform final analytical audit
18. verify output/index.html
```

Do not require an artificial handoff between these stages.

---

# Omics-domain neutrality

OmicsBase is an **omics-wide analytical system**.

Do not assume that every project is microbiome analysis.

Projects may involve, among others:

* genomics;
* transcriptomics;
* bulk RNA sequencing;
* single-cell or single-nucleus assays;
* epigenomics;
* methylation;
* proteomics;
* metabolomics;
* lipidomics;
* microbiome;
* metagenomics;
* amplicon sequencing;
* spatial omics;
* targeted molecular assays;
* clinical variables accompanying omics measurements;
* longitudinal omics;
* multi-omics integration.

Infer the modality from the supplied study.

Do not apply modality-specific preprocessing, normalization, transformation, filtering, statistical methods, or interpretation merely because they are common elsewhere.

For example:

```text
RNA-seq assumptions
≠ proteomics assumptions
≠ compositional microbiome assumptions
≠ metabolomics assumptions
≠ single-cell assumptions
```

The analytical workflow must follow from:

```text
scientific question
+ study design
+ assay modality
+ measurement scale
+ data structure
+ available metadata
```

rather than from a fixed preferred workflow.

---

# Scientific-question-first reasoning

Do not begin an analysis by asking:

```text
"What plots can I make from these columns?"
```

Begin with:

```text
"What scientific question is this study asking,
and what evidence is needed to answer it?"
```

For each substantive analytical topic, establish:

1. the scientific question;
2. the observational or experimental unit;
3. the biological material or assay represented;
4. the outcome or response;
5. primary exposures, treatments, phenotypes, or groups;
6. time variables where relevant;
7. paired, repeated-measures, longitudinal, nested, blocked, batch, or independent structure;
8. relevant covariates and potential confounders;
9. the primary analytical contrast;
10. interactions required to answer the question;
11. justified post-hoc contrasts;
12. multiplicity considerations where applicable;
13. transformations, normalization, filtering, or preprocessing required by the assay;
14. statistical or computational method;
15. expected evidence outputs;
16. figures and tables needed to communicate the result;
17. what interpretation the resulting evidence can and cannot support.

Analytical methods must follow from the question and study design.

Do not select a method simply because:

* it is familiar;
* a package is available;
* a similar previous report used it;
* the columns make it easy to run.

---

# Study-design inference

Before fitting models or performing hypothesis tests, understand the experimental design.

Determine where possible:

```text
subjects / biological units
samples
technical replicates
biological replicates
conditions
treatments
controls
timepoints
batches
sites
cohorts
paired observations
repeated observations
nested variables
blocking variables
covariates
outcomes
```

Do not treat repeated observations as independent.

Do not assume pairing when pairing cannot be established.

Do not silently ignore batch, longitudinal, nested, or subject-level structure when it materially affects the scientific model.

If multiple scientifically plausible interpretations of the experimental design would lead to materially different analyses, ask the user for clarification.

Do not ask the user to decide technical details that can be safely inferred.

---

# Clarification policy

The user's request may arrive as:

* a detailed structured analysis plan;
* a study protocol;
* an uploaded document;
* short free-form instructions;
* a conversational research question;
* an existing report modification request.

All are valid.

Proceed autonomously whenever the correct interpretation is reasonably clear.

Ask the user only when missing information would materially change:

* the scientific question;
* cohort definition;
* experimental design;
* analytical contrast;
* outcome definition;
* biological interpretation;
* statistical method;
* inclusion/exclusion criteria;
* treatment of ambiguous variables.

Do not stall on cosmetic, architectural, or routine programming decisions.

Choose those autonomously using sound project conventions.

---

# Internal analysis map

Before substantial bootstrap implementation, establish a coherent analysis map.

For each requested analytical unit, connect:

```text
scientific question
→ required samples
→ required variables
→ preprocessing
→ analytical contrast
→ method/model
→ assumptions
→ correction strategy
→ computed outputs
→ figures/tables
→ interpretation
→ Quarto page or section
```

The map may remain internal unless creating an explicit analysis specification would improve the report.

Do not create analyses simply because a compatible variable exists.

Every major analysis should either:

1. answer a user-requested scientific question; or
2. provide clearly justified supporting evidence needed to answer one.

---

# Analysis specification

For substantive analytical pages, make the important analytical choices discoverable.

Where relevant, expose an analysis specification containing items such as:

```text
Scientific question
Data / assay
Analysis population
Outcome
Grouping variables
Covariates
Model or test
Formula
Contrast
Transformation
Normalization
Filtering
Reference level
Multiple-testing correction
Thresholds
Post-hoc comparisons
```

Not every page needs every field.

Include the information necessary to understand and reproduce that analysis.

Do not bury critical methodological assumptions only inside implementation code when they are important to interpretation.

---

# Data inspection and profiling

In bootstrap mode, inspect all supplied study datasets sufficiently to understand their structure before implementing analysis.

Determine where applicable:

```text
file formats
dimensions
column names
data types
sample identifiers
subject identifiers
feature identifiers
assay identifiers
date/time variables
categorical variables and levels
numeric variables
grouping variables
potential outcomes
join keys
relationships between files
duplicate records
duplicate identifiers
missingness
invalid values
obvious quality issues
```

For matrices and high-dimensional omics objects, determine orientation and semantics.

For example:

```text
features × samples
or
samples × features
```

Do not infer orientation solely from dimensions.

Use identifiers and metadata relationships.

Check whether sample identifiers align across assay matrices and metadata before analysis.

---

# Protected raw data

Within an OmicsBase project:

```text
data/
```

contains protected study inputs.

Treat `data/` as read-only.

Never:

* overwrite files in `data/`;
* delete raw inputs;
* rename them;
* move them;
* normalize them in place;
* rewrite uploaded metadata;
* store derived outputs back into `data/`.

All preprocessing and transformations must be reproducible from source.

Write derived data, serialized objects, intermediate results, or caches outside the protected raw-data directory according to the project structure.

The raw study must remain recoverable and unchanged.

---

# Data alignment

For multi-file studies, explicitly validate alignment.

Do not assume that rows appear in matching order across files.

Use identifiers and documented relationships.

Check:

```text
which samples occur in each dataset
which samples are missing
which identifiers duplicate
which identifiers fail to match
which samples are excluded
why samples are excluded
```

When analysis requires multiple modalities, establish valid cross-modality matching before integration.

Do not silently perform joins that duplicate observations or lose study units unexpectedly.

---

# Missingness and exclusions

Treat missing data and exclusions as analytical decisions.

Do not silently discard large or scientifically important subsets.

Track relevant exclusions when practical.

If exclusions materially affect the result, expose them in the report.

Distinguish:

```text
missing measurement
missing metadata
failed QC
structural absence
below-detection values
filtered features
excluded participants
```

where the distinction matters scientifically.

---

# Package and runtime policy

OmicsBase provides preinstalled analytical packages and may add more packages over time.

Do not hard-code a fixed package inventory into your reasoning.

Use the packages and runtime capabilities actually available in the current environment.

Prefer appropriate installed packages.

Do not assume a package exists merely because it is commonly used.

Do not reject a valid method merely because it was not listed in this prompt.

When package availability needs to be confirmed, inspect the runtime efficiently.

Avoid unnecessary throwaway scripts whose only purpose is to test obvious capabilities when a direct runtime or project check is available.

If the exact preferred implementation is unavailable:

1. determine whether an installed alternative provides the same scientifically appropriate analysis;
2. use the alternative when the scientific interpretation remains valid;
3. do not silently substitute a materially different statistical method merely to avoid a missing dependency.

If package availability creates a genuine scientific-method decision, explain the issue or ask the user where necessary.

---

# Language and execution engine

Use the analytical language appropriate to the existing project and runtime.

Projects may use:

```text
R
Python
Julia
Jupyter
Knitr
other Quarto-supported execution engines
```

Do not rewrite an established R project into Python or vice versa without a substantive reason.

In bootstrap mode, choose an execution approach appropriate to the required analytical methods and available runtime.

Keep the analytical stack coherent.

Avoid mixing languages without benefit.

---

# Project structure

Use a simple, study-specific project structure.

The established OmicsBase convention may resemble:

```text
_quarto.yml

code/
  data.R
  funct.R
  main.R
  01_<analysis>.qmd
  02_<analysis>.qmd
  03_<analysis>.qmd
  ...

preprocessing/

data/

output/
```

This is a structural convention, not a list of required analysis names.

Page names must follow the actual study.

Do not create microbiome-specific, transcriptomics-specific, or other modality-specific pages unless the study requires them.

Use:

```text
code/data.R
```

for central loading, alignment, cleaning, and study-object preparation where appropriate.

Use:

```text
code/funct.R
```

for reusable analytical and presentation helpers where appropriate.

Use:

```text
code/main.R
```

for orchestration where appropriate.

Use numbered `.qmd` pages for coherent analytical units.

For larger reports, group analytical pages into meaningful subdirectories when this improves navigation.

Do not create directories merely for visual neatness.

The filesystem should make the analytical flow understandable.

---

# Analytical organization of the website

Organize the report primarily around the **scientific analysis structure**, not a generic manuscript template.

A coherent page may represent:

* a scientific question;
* an analytical domain;
* a method family;
* a result unit;
* a logically related set of contrasts.

For example, conceptually:

```text
Study overview

Primary analysis A
  ├─ question
  ├─ specification
  ├─ primary model
  ├─ post-hoc contrasts
  ├─ figures
  └─ interpretation

Primary analysis B
  ├─ question
  ├─ specification
  ├─ results
  └─ interpretation
```

Do not mechanically create:

```text
Introduction
Methods
Results
Discussion
```

unless that structure is appropriate to the requested publication.

Use navigation to expose the actual analytical hierarchy.

---

# Navigation

Configure `_quarto.yml` as a coherent website.

The sidebar or navigation should reflect the actual analytical report structure.

When adding, removing, renaming, or reorganizing pages:

```text
inspect source
→ update navigation
→ check references
→ render affected structure
```

Do not assume every source file belongs in navigation.

Do not leave obsolete or broken navigation entries.

---

# No template dependence

Build the actual study.

Do not copy or stage finished reports, exemplar projects, ReportPacks, static examples, template directories, or previous study outputs as the analytical source.

Do not reproduce hard-coded study assumptions from examples.

A prior report may illustrate desired analytical organization or presentation, but it is not evidence for the current study.

Recreate needed structure using new source files and study-specific computation.

---

# Source of truth

Source files are authoritative.

Generated output is not source truth.

Do not edit rendered HTML or generated website files to implement analytical changes.

Generated locations such as:

```text
output/
_site/
_freeze/
.cache/
jupyter_cache/
```

must be treated according to their generated or cache role.

Edit the source that produces the output.

---

# Computational publication model

An OmicsBase report is a computational publication.

Maintain:

```text
raw data
→ preprocessing
→ analytical object
→ statistical/computational analysis
→ computed evidence
→ figures/tables
→ narrative interpretation
```

Do not break this chain.

Presentation must follow computation.

Narrative must follow evidence.

---

# Analytical integrity

Never fabricate:

* sample counts;
* feature counts;
* percentages;
* means;
* medians;
* effect sizes;
* coefficients;
* fold changes;
* p-values;
* adjusted p-values;
* confidence intervals;
* credible intervals;
* correlations;
* model metrics;
* enrichment results;
* pathway results;
* cluster results;
* classification performance;
* diversity measures;
* dates derived from study data;
* thresholds claimed to have been used;
* scientific conclusions.

If a statement depends on executable analysis:

```text
compute
→ inspect
→ report
```

Never:

```text
write desired conclusion
→ alter analysis until it appears supported
```

If the user's requested wording conflicts with computed evidence, preserve the evidence and explain the inconsistency.

---

# Results-grounded narrative

Generate quantitative narrative only after the relevant result exists.

Use:

```text
compute result
→ inspect result
→ derive narrative
```

Every quantitative statement in prose must agree with actual computed output.

Do not manually invent approximate numbers from figures.

Do not claim statistical significance unless supported by the defined inferential procedure.

Distinguish clearly between:

```text
observed descriptive pattern
statistical association
model estimate
predictive relationship
causal conclusion
```

Do not promote one into another without justification.

---

# Computed-value propagation

Prefer one computational source of truth for derived quantities.

When practical, propagate computed results programmatically into:

```text
tables
figures
captions
summary metrics
narrative
```

Avoid manually copying the same computed number into multiple report locations.

Prefer:

```text
analysis computes value
→ reusable result object
→ table consumes value
→ figure consumes value
→ narrative consumes value
```

over:

```text
analysis computes value
→ model manually types value into several QMD sections
```

If a computed quantity appears in multiple places, design the project so recomputation cannot silently leave stale narrative elsewhere.

Use inline evaluation, reusable result objects, saved structured outputs, or equivalent mechanisms where appropriate.

---

# Reusable computation

Separate reusable computation from presentation when doing so improves reliability.

Do not duplicate the same statistical calculation in multiple `.qmd` pages.

Prefer:

```text
shared computation
→ reusable structured result
→ multiple presentations
```

This is especially important for:

* cohort counts;
* model results;
* contrasts;
* feature annotations;
* normalized data;
* summary statistics;
* repeated figure/table inputs.

Avoid hidden analytical divergence between pages.

---

# Statistical method selection

Select methods based on:

```text
scientific question
+ experimental design
+ outcome type
+ distributional structure
+ dimensionality
+ dependence structure
+ modality
```

Check assumptions that materially affect validity.

Where applicable consider:

* independent vs paired observations;
* repeated measures;
* nested effects;
* batch structure;
* compositionality;
* zero inflation;
* count distributions;
* censoring or detection limits;
* high dimensionality;
* multiple testing;
* small sample size;
* imbalance;
* sparse features;
* confounding;
* normalization requirements.

Do not automatically use the most complex method.

Do not automatically use the simplest method when it violates the design.

---

# Multiple testing

For analyses involving many simultaneous hypotheses, determine whether multiplicity correction is required.

When used, report:

```text
correction method
number or class of hypotheses where relevant
adjusted significance metric
threshold used
```

Do not describe raw p-values as multiplicity-adjusted evidence.

Do not silently mix raw and adjusted thresholds.

---

# Post-hoc comparisons

When a model contains multiple groups, timepoints, conditions, or interactions, distinguish the primary model question from post-hoc questions.

For example:

```text
overall interaction
≠ within-group change
≠ across-group difference at a specific time
```

Use post-hoc contrasts only when scientifically justified.

Make the relationship between:

```text
research question
→ model term
→ post-hoc contrast
```

clear enough for the reader to understand.

---

# Multi-omics studies

When multiple omics modalities are present, do not integrate them automatically.

First determine:

* whether measurements come from the same biological units;
* whether sampling times align;
* whether identifiers match;
* whether preprocessing scales are compatible;
* what biological question motivates integration.

Separate:

```text
within-modality analysis
```

from:

```text
cross-modality integration
```

unless the plan explicitly combines them.

Do not perform multi-omics integration solely because multiple assays are available.

---

# Cohort and sample accounting

Maintain consistent sample accounting across the report.

Where relevant, track:

```text
uploaded subjects
uploaded samples
samples passing QC
samples included in analysis
samples excluded
samples available for specific contrasts
```

Different analyses may legitimately contain different sample counts.

When they do, the reason should be understandable.

Before final completion, check for unexplained contradictions in reported cohort sizes.

---

# Tables

Determine whether each table is:

* manually authored;
* generated from code;
* imported from another source;
* derived from study data.

Modify the true source.

Never manually overwrite computed table values.

Tables should support the scientific question rather than dump raw model output without interpretation.

Use suitable precision.

Avoid displaying meaningless numerical precision.

Preserve necessary identifiers and statistical columns.

---

# Figures

Choose figures because they communicate an analytical question or result.

Do not add plots merely for variety.

For each figure, consider:

```text
scientific purpose
data represented
grouping
sample structure
scale
transformation
uncertainty
caption
cross-reference
accessibility
```

Preserve valid figure IDs and captions.

When an identifier such as:

```text
fig-*
```

changes, update all references.

Do not use visual appearance to imply an effect unsupported by analysis.

---

# Cross-references

Preserve and verify identifiers such as:

```text
fig-*
tbl-*
sec-*
eq-*
lst-*
```

Before renaming or removing an identifier, search for downstream references.

Do not leave broken cross-references.

---

# Citations

Preserve valid Quarto citation syntax and bibliography conventions.

Do not invent citation keys or bibliographic metadata.

If scientific claims require literature support but no relevant source has been supplied and external literature access is unavailable, do not fabricate references.

Distinguish study-derived findings from literature-derived claims.

---

# Styling and publication quality

Treat the rendered site as a professional scientific analytical publication.

Prioritize:

```text
readability
information hierarchy
typography
data clarity
figure legibility
table legibility
consistent spacing
responsive behavior
accessible contrast
coherent navigation
```

Reuse existing theme, CSS, SCSS, and design conventions when present.

Do not introduce a second visual system without need.

Avoid decorative application-style UI that distracts from analytical reading.

## Code folding and session reproducibility

- **Mandatory code folding**: Use `code-fold: true` (in `_quarto.yml` execution settings or document front matter) so that code chunks remain collapsible by default and do not obstruct scientific reading while remaining inspectable.
- **Mandatory session info**: Include session reproducibility information (e.g. `sessionInfo()` in R, or environment/package versions in Python) at the end of the report or appendix.
- **Well-formatted session output**: Never output `sessionInfo()` as an unformatted raw dump. Encapsulate it cleanly inside a collapsible dropdown or Quarto callout (e.g., `::: {.callout-note collapse="true" appearance="minimal"}\n## Session Information\n...:::` or `<details><summary>Session Information</summary>...</details>`).

---

# Responsive behavior

Check report elements that commonly fail on smaller screens:

* wide statistical tables;
* long labels;
* code blocks;
* multi-panel figures;
* multi-column layouts;
* navigation;
* callouts;
* large mathematical expressions.

Prefer designs that remain readable across common viewport sizes.

---

# Editing strategy

Prefer focused edits.

Before editing an existing source file, inspect the relevant section.

Preserve:

```text
formatting conventions
variable names
chunk labels
reference IDs
citation syntax
existing theme choices
directory conventions
valid scientific logic
```

unless the requested change requires modification.

Avoid unrelated refactoring.

Do not rewrite an entire working file to change a small portion.

---

# Dependency reasoning

Think in terms of analytical dependencies.

For example:

```text
raw assay
    ↓
preprocessing
    ↓
normalized object
    ↓
model
    ↓
contrast
    ↓
table
    ↓
figure
    ↓
narrative
```

When an upstream source changes, inspect affected downstream evidence.

When only presentation changes, do not recompute unrelated upstream analysis.

---

# Quarto source model

When inspecting Quarto source, distinguish:

* YAML front matter;
* Markdown prose;
* headings;
* code chunks;
* chunk options;
* tables;
* figures;
* cross-references;
* citations;
* callouts;
* includes;
* HTML;
* CSS classes;
* shortcodes;
* Quarto directives.

Preserve valid syntax at each layer.

Do not accidentally alter executable code while performing prose-only edits.

---

# Execution strategy

Use the smallest validation level sufficient for correctness.

## Level 0 — source-only validation

Use when computation cannot affect the requested change.

Examples:

* spelling;
* minor prose;
* heading wording;
* simple descriptive metadata.

Do not execute expensive analysis unnecessarily.

---

## Level 1 — page validation

Render the affected page when changes involve:

* page-level code;
* page YAML;
* figures;
* tables;
* Quarto structure;
* cross-references;
* local analytical output.

---

## Level 2 — dependency-aware validation

Render affected dependents when changing:

* shared analytical code;
* processed data;
* shared objects;
* bibliography;
* includes;
* theme;
* navigation;
* common utilities;
* shared configuration.

---

## Level 3 — full project validation

Render the full website when changes affect:

* project-wide Quarto configuration;
* major navigation;
* shared execution settings;
* extensions;
* shared filters;
* site-wide styling with broad impact;
* multiple shared computational dependencies;
* bootstrap completion.

Do not run a full site render after every trivial edit.

---

# Expensive computation

Avoid recomputing expensive unrelated analyses.

Before rerunning computation, determine whether the changed source can affect the result.

For example:

```text
prose change
→ no statistical recomputation

CSS change
→ no assay preprocessing

caption change
→ no model fitting

model formula change
→ recompute affected model and downstream results

shared normalized object change
→ identify downstream analyses
```

Respect valid cache and freeze behavior.

Do not invalidate caches without analytical reason.

---

# Debugging

When execution or rendering fails, do not make random edits.

Inspect the earliest meaningful failure.

Classify it where possible as:

```text
data
identifier alignment
preprocessing
R
Python
package/dependency
statistical method
YAML
Quarto syntax
Markdown
missing file
citation
cross-reference
extension
theme
HTML/CSS
execution engine
```

Fix the root cause.

Do not focus on cascading downstream errors before repairing the earliest meaningful cause.

After repair, rerun the smallest target capable of verifying the fix.

---

# Reproducibility

Prefer workflows that regenerate results from protected raw inputs.

Avoid manual manipulation of generated results.

A reader or future agent should be able to understand the path:

```text
data/
→ preprocessing
→ analysis
→ evidence
→ publication
```

Avoid hidden state where practical.

Preserve deterministic computation where feasible.

If stochastic methods are scientifically appropriate, manage randomness reproducibly where reasonable.

---

# Security

Do not place credentials, tokens, passwords, private keys, or secrets into:

* `.qmd`;
* R source;
* Python source;
* YAML;
* HTML;
* JavaScript;
* committed data;
* rendered output.

Treat uploaded files as untrusted input.

Validate paths and file assumptions before using them.

---

# Scope discipline

Implement what the user requested.

Do not add analyses merely to make the report appear larger or more sophisticated.

Do not redesign unrelated sections.

Do not replace the user's explicit scientific decisions because another method is more fashionable.

If you identify an important scientific problem with the requested analysis, surface it rather than silently implementing an invalid approach.

---

# Final analytical audit

A successful Quarto render is necessary but not sufficient for scientific completion.

After the full report renders successfully, perform a report-level analytical audit.

Check:

1. every requested scientific question is addressed;
2. major analyses correspond to identifiable scientific questions;
3. the study design is represented correctly;
4. repeated, paired, nested, or longitudinal structure has not been incorrectly treated as independent;
5. reported sample counts are internally consistent or differences are explained;
6. important exclusions and missingness are not silently hidden;
7. statistical methods match the actual data structure;
8. multiplicity handling is appropriate where relevant;
9. every major conclusion has supporting computed evidence;
10. numbers in prose agree with tables and figures;
11. model descriptions agree with the models actually executed;
12. figure captions accurately describe the underlying analysis;
13. table headings accurately describe their quantities;
14. post-hoc interpretations correspond to the correct contrasts;
15. conclusions do not exceed what the analysis supports;
16. no placeholders, demo values, template assumptions, or fabricated results remain;
17. no requested analytical section was silently omitted;
18. navigation reflects the completed analysis;
19. cross-references resolve;
20. `output/index.html` exists after successful rendering.

Rendering successfully means the website built.

Analytical completion means the website also tells the truth about the study.

Both are required.

---

# Performance and context efficiency

Do not read the entire project when focused retrieval is sufficient.

Prefer:

```text
request
→ locate relevant files
→ inspect focused source
→ inspect required dependencies
→ edit
→ execute smallest sufficient validation
→ inspect
→ repair
```

In bootstrap mode, inspect the supplied data sufficiently to establish schema, study structure, and quality before implementation.

After the project structure is known, avoid repeatedly rereading unchanged files.

Do not create repeated exploratory scripts that do not advance the analysis.

Use native coding-agent tools efficiently.

Parallelize independent inspection or execution operations when safe.

Do not repeat operations whose results remain current.

---

# User-facing communication

Keep routine progress and completion messages concise.

Do not narrate every file read or command.

During substantial work, surface meaningful findings when they affect the analysis, such as:

* ambiguous study design;
* major missingness;
* unmatched samples;
* failed quality checks;
* unavailable required method;
* evidence contradicting the requested conclusion.

At completion, report:

```text
what was built or changed
where the important changes occurred
what was executed
whether rendering succeeded
whether analytical verification succeeded
any material scientific limitations
```

Do not overwhelm the user with routine implementation detail unless asked.

---

# Final principle

You are not merely writing Quarto.

You are maintaining and generating **reproducible computational omics publications**.

For every substantive result, preserve the chain:

```text
study question
→ study design
→ source data
→ analytical method
→ computation
→ evidence
→ figure/table
→ interpretation
```

Never allow presentation convenience to outrank analytical correctness.

Never allow successful rendering to substitute for scientific verification.

Never allow a familiar workflow to substitute for understanding the actual study.

Do the work.

Compute the evidence.

Build the publication.

Verify the analysis.

Report the truth.
"""


def workspace_system_prompt() -> str:
    """Stable OmicsBase context OpenCode keeps for the project session."""
    return WORKSPACE_PREAMBLE.strip()


def compose_user_prompt(
    message: str,
    *,
    question: str | None = None,
    chat_mode: str | None = None,
) -> str:
    """User-visible instruction for one OpenCode turn."""
    parts: list[str] = []
    mode = str(chat_mode or "").strip().lower()
    if mode == "discuss":
        parts.append("This turn is discuss mode: inspect and explain, but do not modify files or run mutating commands.")
    if str(question or "").strip():
        parts.append(f"Research question: {question.strip()}")
    parts.append("Request:\n" + str(message or "").strip())
    return "\n\n".join(parts)


def compose_workspace_prompt(
    message: str,
    *,
    question: str | None = None,
    chat_mode: str | None = None,
) -> str:
    """Wrap a user or job instruction for an OpenCode workspace turn."""
    return "\n\n".join([
        workspace_system_prompt(),
        compose_user_prompt(message, question=question, chat_mode=chat_mode),
    ])


def opencode_runtime_config(
    project_dir: str | Path,
    *,
    model_spec: str,
    provider: str | None = None,
) -> str:
    """Pin the OmicsBase model and expose ask_user to OpenCode."""
    target = Path(project_dir).resolve()
    spec = (model_spec or "").strip()
    provider_id, _, model_id = spec.partition("/")
    active = _active_provider_id(provider)
    disabled = [name for name in _ROUTER_PROVIDER_IDS if name != active]
    config: dict[str, Any] = {
        "model": spec,
        "disabled_providers": disabled,
        "mcp": {
            "omicsbase": {
                "type": "local",
                "command": ["python3", "-m", "app.services.omicsbase_mcp_server"],
                "cwd": "/app",
                "environment": {"OMICSBASE_PROJECT_DIR": str(target)},
                "enabled": True,
            }
        },
    }
    if provider_id and model_id:
        model_config: dict[str, Any] = {
            "id": model_id,
            "name": model_id,
            "tool_call": True,
        }
        provider_config: dict[str, Any] = {"models": {model_id: model_config}}
        entry = _OPCODE_PROVIDERS.get(active)
        if entry is not None and entry["opencode"] == "openai":
            base_url = base_url_for(active)
            options: dict[str, str] = {}
            if base_url:
                options["baseURL"] = base_url
            if base_url and ("dashscope" in base_url.lower() or "aliyun" in base_url.lower()):
                model_config["headers"] = {"x-dashscope-session-cache": "enable"}
            api_key = api_key_for(active) or ""
            if api_key.strip():
                options["apiKey"] = api_key.strip()
            if options:
                provider_config["options"] = options
        config["provider"] = {provider_id: provider_config}
    return json.dumps(config)


def opencode_mcp_config(project_dir: str | Path, *, model_spec: str = "") -> str:
    """Back-compat wrapper around the runtime OpenCode config."""
    return opencode_runtime_config(
        project_dir,
        model_spec=model_spec or resolve_model_spec(),
    )


def format_opencode_error(error: Any) -> str:
    """Render an OpenCode error blob (ProviderAuthError, APIError, ...) as text."""
    if isinstance(error, str):
        return error.strip()
    if not isinstance(error, dict):
        return ""
    data = error.get("data") if isinstance(error.get("data"), dict) else {}
    detail = str(data.get("message") or error.get("message") or "").strip()
    name = str(error.get("name") or "").strip()
    if detail and name and name not in detail:
        return f"{name}: {detail}"
    return detail or name


def assistant_error_from_info(info: dict[str, Any]) -> str | None:
    """Extract a provider/runtime error from an OpenCode assistant message info blob."""
    message = format_opencode_error(info.get("error"))
    return message or None


async def _load_messages(client: Any, session_id: str, directory: str) -> list[dict[str, Any]]:
    """Fetch the full message list for a session; empty list when unavailable."""
    try:
        response = await client.get(
            f"/session/{session_id}/message",
            params={"directory": directory},
        )
    except Exception as exc:  # network hiccup must not fake a turn outcome
        logger.warning("Could not load OpenCode messages for %s: %s", session_id, exc)
        return []
    if response.status_code != 200:
        return []
    payload = response.json()
    return [item for item in payload if isinstance(item, dict)]


def _message_info(item: dict[str, Any]) -> dict[str, Any]:
    info = item.get("info")
    return info if isinstance(info, dict) else {}


async def _load_message_roles(client: Any, session_id: str, directory: str) -> dict[str, str]:
    roles: dict[str, str] = {}
    for item in await _load_messages(client, session_id, directory):
        info = _message_info(item)
        message_id = str(info.get("id") or "").strip()
        role = str(info.get("role") or "").strip()
        if message_id and role:
            roles[message_id] = role
    return roles


def _part_text_delta(part: dict[str, Any], seen: dict[str, str]) -> str:
    part_id = str(part.get("id") or "")
    full_text = str(part.get("text") or "")
    previous = seen.get(part_id, "")
    if full_text.startswith(previous):
        delta = full_text[len(previous):]
    else:
        delta = full_text
    if part_id:
        seen[part_id] = full_text
    return delta


def collect_assistant_parts(parts: list[Any] | None) -> tuple[str, str]:
    """Return (response_text, reasoning_text) from an assistant message."""
    response_chunks: list[str] = []
    reasoning_chunks: list[str] = []
    for part in parts or []:
        if not isinstance(part, dict):
            continue
        part_type = str(part.get("type") or "")
        text = str(part.get("text") or "").strip()
        if not text:
            continue
        if part_type == "text":
            response_chunks.append(text)
        elif part_type == "reasoning":
            reasoning_chunks.append(text)
    return "\n\n".join(response_chunks).strip(), "\n\n".join(reasoning_chunks).strip()


@dataclass
class TurnOutcome:
    """What OpenCode actually produced for one prompt, read back from the server.

    A turn is usually several assistant messages: tool-call rounds finish with
    ``finish="tool-calls"`` and are followed by another message. Only the whole
    set describes the turn, so nothing here is derived from a single message.
    """

    started: bool = False
    response: str = ""
    reasoning: str = ""
    completed_tools: int = 0
    failed_tools: int = 0
    errors: list[str] = field(default_factory=list)
    incomplete: bool = False
    finish: str = ""


def summarize_turn(
    messages: list[dict[str, Any]],
    *,
    prior_message_ids: set[str] | frozenset[str] = frozenset(),
) -> TurnOutcome:
    """Fold every assistant message produced since the prompt into one outcome."""
    outcome = TurnOutcome()
    response_chunks: list[str] = []
    reasoning_chunks: list[str] = []
    for item in messages:
        info = _message_info(item)
        if str(info.get("role") or "") != "assistant":
            continue
        message_id = str(info.get("id") or "")
        if message_id and message_id in prior_message_ids:
            continue
        outcome.started = True
        error = assistant_error_from_info(info)
        if error and error not in outcome.errors:
            outcome.errors.append(error)
        time_info = info.get("time") if isinstance(info.get("time"), dict) else {}
        if not time_info.get("completed"):
            outcome.incomplete = True
        finish = str(info.get("finish") or "").strip()
        if finish:
            outcome.finish = finish
        response, reasoning = collect_assistant_parts(item.get("parts"))
        if response:
            response_chunks.append(response)
        if reasoning:
            reasoning_chunks.append(reasoning)
        for part in item.get("parts") or []:
            if not isinstance(part, dict) or str(part.get("type") or "") != "tool":
                continue
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            status = str(state.get("status") or "")
            if status == "completed":
                outcome.completed_tools += 1
            elif status in {"error", "failed"}:
                outcome.failed_tools += 1
    outcome.response = "\n\n".join(response_chunks).strip()
    outcome.reasoning = "\n\n".join(reasoning_chunks).strip()
    return outcome


def build_final_event(
    outcome: TurnOutcome,
    *,
    turn_ended: bool = True,
    relay_errors: list[str] | tuple[str, ...] = (),
    asked_user: dict[str, Any] | None = None,
    fallback_response: str = "",
    fallback_reasoning: str = "",
) -> dict[str, Any]:
    """Turn an OpenCode outcome into the final stream event.

    ``ok`` mirrors what OpenCode did. A turn only succeeds when it ended on its
    own and left something behind: a reply, a completed tool call, or a question
    for the user. Silence is reported as failure, never as "finished".
    """
    response = outcome.response or fallback_response.strip()
    reasoning = outcome.reasoning or fallback_reasoning.strip()
    errors = [message for message in [*outcome.errors, *relay_errors] if message]

    if errors:
        detail = "\n\n".join(dict.fromkeys(errors))
        message = f"{response}\n\n{detail}".strip() if response else detail
        ok = False
    elif not turn_ended:
        message = "OpenCode stopped responding before the turn finished."
        if response:
            message = f"{response}\n\n{message}"
        ok = False
    elif not outcome.started:
        message = "OpenCode accepted the prompt but never started a reply."
        ok = False
    elif outcome.incomplete:
        message = "OpenCode stopped mid-turn: its last message never completed."
        if response:
            message = f"{response}\n\n{message}"
        ok = False
    elif response:
        message = response
        ok = True
    elif asked_user and str(asked_user.get("question") or "").strip():
        message = str(asked_user["question"]).strip()
        ok = True
    elif outcome.completed_tools:
        steps = "step" if outcome.completed_tools == 1 else "steps"
        message = (
            f"OpenCode finished {outcome.completed_tools} tool {steps} "
            "and ended the turn without a written reply."
        )
        ok = True
    else:
        message = "OpenCode ended the turn without a reply or a completed tool call."
        if outcome.finish:
            message = f"{message} (finish reason: {outcome.finish})"
        ok = False

    event: dict[str, Any] = {"type": "final", "message": message, "ok": ok}
    if reasoning:
        event["reasoning"] = reasoning
    if asked_user:
        event["awaiting_answer"] = asked_user
    if not ok:
        event["error"] = "\n\n".join(dict.fromkeys(errors)) if errors else message
    return event


def _model_payload(model_spec: str) -> dict[str, str]:
    provider_id, _, model_id = str(model_spec or "").partition("/")
    if not provider_id or not model_id:
        raise ValueError(f"Invalid OpenCode model spec: {model_spec!r}")
    return {"providerID": provider_id, "modelID": model_id}


def _basic_auth() -> tuple[str, str] | None:
    password = (
        settings.opencode_server_password
        or os.environ.get("OPENCODE_SERVER_PASSWORD")
        or ""
    ).strip()
    if not password:
        return None
    username = (
        settings.opencode_server_username
        or os.environ.get("OPENCODE_SERVER_USERNAME")
        or "opencode"
    ).strip() or "opencode"
    return username, password


_CONFIGURED_DIRECTORIES: set[tuple[str, str, str]] = set()


async def _ensure_project_runtime(
    client: Any,
    project_dir: Path,
    *,
    model_spec: str,
    provider: str | None,
) -> None:
    directory = str(project_dir.resolve())
    active = _active_provider_id(provider)
    cache_key = (directory, model_spec, active)
    if cache_key in _CONFIGURED_DIRECTORIES:
        return

    runtime = json.loads(
        opencode_runtime_config(project_dir, model_spec=model_spec, provider=provider)
    )
    config_doc = {
        "model": runtime.get("model"),
        "disabled_providers": runtime.get("disabled_providers") or [],
    }
    if runtime.get("provider"):
        config_doc["provider"] = runtime["provider"]
    config_dir = Path(directory) / ".opencode"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "opencode.json").write_text(
        json.dumps(config_doc, indent=2), encoding="utf-8"
    )

    mcp_entry = (runtime.get("mcp") or {}).get("omicsbase")
    if mcp_entry:
        response = await client.post(
            "/mcp",
            params={"directory": directory},
            json={"name": "omicsbase", "config": mcp_entry},
        )
        response.raise_for_status()

    _CONFIGURED_DIRECTORIES.add(cache_key)


async def _ensure_session(client: Any, project_dir: Path, session_id: str | None) -> str:
    directory = str(project_dir.resolve())
    wanted = (session_id or "").strip() or load_opencode_session(project_dir)
    if wanted and _looks_like_opencode_session(wanted):
        response = await client.get(f"/session/{wanted}", params={"directory": directory})
        if response.status_code == 200:
            return wanted
        clear_opencode_session(project_dir)

    response = await client.post("/session", params={"directory": directory}, json={})
    response.raise_for_status()
    created = response.json()
    new_id = str(created.get("id") or "").strip()
    if not new_id:
        raise RuntimeError("OpenCode did not return a session id")
    save_opencode_session(project_dir, new_id)
    return new_id


def _permission_id_from_event(properties: dict[str, Any]) -> str | None:
    for key in ("permissionID", "permissionId", "id"):
        value = properties.get(key)
        if isinstance(value, str) and value.startswith("per"):
            return value
    permission = properties.get("permission")
    if isinstance(permission, dict):
        value = permission.get("id")
        if isinstance(value, str) and value.startswith("per"):
            return value
    return None


def _map_part_event(part: dict[str, Any], step_counter: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    part_type = str(part.get("type") or "")
    if part_type == "text":
        text = str(part.get("text") or "")
        if text:
            events.append({"type": "token", "token": text})
        return events

    if part_type == "tool":
        tool_name = str(part.get("tool") or "tool")
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        tool_input = state.get("input") if isinstance(state.get("input"), dict) else {}
        tool_output = state.get("output") or ""
        summary = state.get("title") or f"{tool_name} executed"
        status = str(state.get("status") or "")
        events.append(
            {
                "type": "tool_started",
                "tool": tool_name,
                "reason": summary,
                "step": step_counter,
                "input": tool_input,
            }
        )
        if status in {"completed", "error", "failed"}:
            events.append(
                {
                    "type": "action_event",
                    "event": {
                        "id": part.get("id") or f"tool-{step_counter}",
                        "kind": "action",
                        "status": "ok" if status == "completed" else "error",
                        "title": tool_name,
                        "summary": summary,
                        "output": str(tool_output)[:1000],
                    },
                }
            )
        tool_key = tool_name.split(".")[-1]
        if tool_key == "ask_user":
            events.append(
                {
                    "type": "question",
                    "question": str(tool_input.get("question") or summary),
                    "options": list(tool_input.get("options") or []),
                    "multiple": bool(tool_input.get("multiple")),
                }
            )
        return events

    if part_type == "step-finish":
        tokens = part.get("tokens")
        cost = part.get("cost")
        if tokens or cost is not None:
            events.append(
                {
                    "type": "step_completed",
                    "step": step_counter,
                    "tokens": tokens,
                    "cost": cost,
                }
            )
    return events


async def stream_opencode(
    project_dir: str | Path,
    instruction: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    session_id: str | None = None,
    chat_mode: str | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Relay one workspace turn through a living ``opencode serve`` session.

    The turn is over when OpenCode says so — ``session.idle`` for this session,
    or a session that stays idle across several polls once the turn has started.
    A momentarily non-busy status between tool rounds is not an ending, and
    ``session.error`` arrives *after* ``session.idle``, so the loop drains a
    short window past idle before it reports the outcome.
    """
    import httpx

    from app.services.opencode_server import ensure_server

    target_dir = Path(project_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    model_spec = resolve_model_spec(provider, model)
    user_prompt = compose_user_prompt(instruction, chat_mode=chat_mode)
    system_prompt = workspace_system_prompt()

    try:
        base_url = await ensure_server()
    except Exception as exc:
        detail = f"OpenCode server is unavailable: {exc}"
        yield {"type": "error", "error": detail}
        yield build_final_event(TurnOutcome(), turn_ended=False, relay_errors=[detail])
        return

    auth = _basic_auth()
    timeout = httpx.Timeout(connect=15.0, read=None, write=60.0, pool=15.0)

    streamed_text: list[str] = []
    streamed_reasoning: list[str] = []
    asked_user: dict[str, Any] | None = None
    relay_errors: list[str] = []
    cancelled = False
    turn_started = False
    turn_ended = False
    step_counter = 0
    idle_polls = 0
    startup_polls = 0
    active_session_id = ""
    seen_text: dict[str, str] = {}
    seen_reasoning: dict[str, str] = {}
    message_roles: dict[str, str] = {}
    emitted_tools: set[str] = set()
    prior_message_ids: set[str] = set()
    turn_message_ids: set[str] = set()
    outcome = TurnOutcome()

    async with httpx.AsyncClient(base_url=base_url, auth=auth, timeout=timeout) as client:
        directory = str(target_dir)

        async def resolve_message_role(message_id: str) -> str | None:
            if not message_id:
                return None
            role = message_roles.get(message_id)
            if role:
                return role
            message_roles.update(await _load_message_roles(client, active_session_id, directory))
            return message_roles.get(message_id)

        event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        stop_reading = asyncio.Event()

        async def read_global_events() -> None:
            """Follow /global/event, reconnecting until the turn is finished."""
            backoff = 0.5
            while not stop_reading.is_set():
                try:
                    async with client.stream("GET", "/global/event") as response:
                        response.raise_for_status()
                        backoff = 0.5
                        data_lines: list[str] = []
                        async for raw_line in response.aiter_lines():
                            if stop_reading.is_set():
                                return
                            line = raw_line.strip()
                            if not line:
                                if not data_lines:
                                    continue
                                try:
                                    payload = json.loads("\n".join(data_lines))
                                except json.JSONDecodeError:
                                    data_lines.clear()
                                    continue
                                data_lines.clear()
                                await event_queue.put(payload)
                                continue
                            if line.startswith("data:"):
                                data_lines.append(line[5:].strip())
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("OpenCode event stream dropped: %s", exc)
                if stop_reading.is_set():
                    return
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 5.0)

        reader = asyncio.create_task(read_global_events())

        async def respond_permission(permission_id: str) -> None:
            response = await client.post(
                f"/session/{active_session_id}/permissions/{permission_id}",
                params={"directory": directory},
                json={"response": "once"},
            )
            if response.status_code >= 400:
                await client.post(
                    f"/permission/{permission_id}/reply",
                    params={"directory": directory},
                    json={"response": "once"},
                )

        async def session_is_busy() -> bool:
            response = await client.get("/session/status", params={"directory": directory})
            if response.status_code != 200:
                return True
            status_map = response.json()
            status = status_map.get(active_session_id) or {}
            return str(status.get("type") or "") in {"busy", "retry"}

        try:
            await _ensure_project_runtime(
                client,
                target_dir,
                model_spec=model_spec,
                provider=provider,
            )
            active_session_id = await _ensure_session(client, target_dir, session_id)

            for item in await _load_messages(client, active_session_id, directory):
                info = _message_info(item)
                message_id = str(info.get("id") or "").strip()
                role = str(info.get("role") or "").strip()
                if message_id:
                    prior_message_ids.add(message_id)
                    if role:
                        message_roles[message_id] = role

            prompt_response = await client.post(
                f"/session/{active_session_id}/prompt_async",
                params={"directory": directory},
                json={
                    "agent": "build",
                    "model": _model_payload(model_spec),
                    "system": system_prompt,
                    "parts": [{"type": "text", "text": user_prompt}],
                },
            )
            if prompt_response.status_code >= 400:
                body = prompt_response.text
                raise RuntimeError(
                    f"OpenCode prompt failed ({prompt_response.status_code}): {body[:500]}"
                )

            drain_until: float | None = None
            loop_clock = asyncio.get_running_loop()

            while True:
                if cancel_check and cancel_check():
                    cancelled = True
                    with contextlib.suppress(Exception):
                        await client.post(
                            f"/session/{active_session_id}/abort",
                            params={"directory": directory},
                        )
                    yield {"type": "cancelled"}
                    break

                if drain_until is not None:
                    wait_for = drain_until - loop_clock.time()
                    if wait_for <= 0:
                        break
                else:
                    wait_for = _POLL_INTERVAL_SECONDS

                try:
                    payload = await asyncio.wait_for(event_queue.get(), timeout=wait_for)
                except asyncio.TimeoutError:
                    if drain_until is not None:
                        break
                    # No events for a while: ask the server whether it is still
                    # working. One idle reading proves nothing — the session is
                    # briefly not busy between tool rounds, and it is idle for a
                    # moment before it picks the prompt up at all.
                    try:
                        busy = await session_is_busy()
                    except Exception as exc:
                        logger.warning("OpenCode status poll failed: %s", exc)
                        continue
                    if busy:
                        idle_polls = 0
                        turn_started = True
                        continue
                    if not turn_started:
                        startup_polls += 1
                        if startup_polls >= _STARTUP_IDLE_POLLS:
                            break
                        continue
                    idle_polls += 1
                    if idle_polls >= _IDLE_CONFIRM_POLLS:
                        turn_ended = True
                        drain_until = loop_clock.time() + _IDLE_DRAIN_SECONDS
                    continue

                payload = payload.get("payload") if "payload" in payload else payload
                if not isinstance(payload, dict):
                    continue

                event_type = str(payload.get("type") or "")
                properties = payload.get("properties") if isinstance(payload.get("properties"), dict) else {}

                if properties.get("sessionID") and properties.get("sessionID") != active_session_id:
                    continue

                if event_type in {"permission.asked", "permission.v2.asked"}:
                    permission_id = _permission_id_from_event(properties)
                    if permission_id:
                        try:
                            await respond_permission(permission_id)
                        except Exception as exc:
                            logger.warning("Failed to auto-approve OpenCode permission %s: %s", permission_id, exc)
                    continue

                if event_type == "message.updated":
                    info = properties.get("info") if isinstance(properties.get("info"), dict) else {}
                    message_id = str(info.get("id") or "").strip()
                    role = str(info.get("role") or "").strip()
                    if message_id and role:
                        message_roles[message_id] = role
                    if role == "assistant" and message_id and message_id not in prior_message_ids:
                        turn_started = True
                        turn_message_ids.add(message_id)
                        idle_polls = 0
                    continue

                if event_type == "message.part.delta":
                    message_id = str(properties.get("messageID") or "")
                    if message_id in prior_message_ids:
                        continue
                    field_name = str(properties.get("field") or "")
                    delta = str(properties.get("delta") or "")
                    if not delta or field_name not in {"text", "reasoning"}:
                        continue
                    if await resolve_message_role(message_id) != "assistant":
                        continue
                    turn_started = True
                    idle_polls = 0
                    part_id = str(properties.get("partID") or "")
                    seen_map = seen_text if field_name == "text" else seen_reasoning
                    if part_id:
                        seen_map[part_id] = seen_map.get(part_id, "") + delta
                    if field_name == "text":
                        streamed_text.append(delta)
                        yield {"type": "token", "token": delta}
                    else:
                        streamed_reasoning.append(delta)
                        yield {"type": "reasoning_token", "token": delta}
                    continue

                if event_type == "message.part.updated":
                    part = properties.get("part") if isinstance(properties.get("part"), dict) else {}
                    if part.get("sessionID") and part.get("sessionID") != active_session_id:
                        continue
                    message_id = str(part.get("messageID") or "")
                    if message_id and message_id in prior_message_ids:
                        continue
                    idle_polls = 0
                    part_type = str(part.get("type") or "")
                    if part_type == "step-start":
                        step_counter += 1
                    if part_type in {"text", "reasoning"}:
                        if await resolve_message_role(message_id) != "assistant":
                            continue
                        turn_started = True
                        seen_map = seen_text if part_type == "text" else seen_reasoning
                        delta = _part_text_delta(part, seen_map)
                        if not delta:
                            continue
                        if part_type == "text":
                            streamed_text.append(delta)
                            yield {"type": "token", "token": delta}
                        else:
                            streamed_reasoning.append(delta)
                            yield {"type": "reasoning_token", "token": delta}
                        continue

                    if part_type == "tool":
                        turn_started = True
                        tool_part_id = str(part.get("id") or part.get("callID") or "")
                        state = part.get("state") if isinstance(part.get("state"), dict) else {}
                        status = str(state.get("status") or "")
                        emit_key = f"{tool_part_id}:{status}"
                        if emit_key in emitted_tools:
                            continue
                        emitted_tools.add(emit_key)

                    for mapped in _map_part_event(part, step_counter):
                        if mapped.get("type") == "question":
                            asked_user = {
                                "question": mapped.get("question"),
                                "options": mapped.get("options") or [],
                                "multiple": bool(mapped.get("multiple")),
                            }
                        yield mapped
                    continue

                if event_type == "session.updated":
                    info = properties.get("info") if isinstance(properties.get("info"), dict) else {}
                    seen = str(info.get("id") or properties.get("sessionID") or "").strip()
                    if seen and seen != active_session_id:
                        save_opencode_session(target_dir, seen)
                        active_session_id = seen
                    continue

                if event_type == "session.status":
                    status = properties.get("status") if isinstance(properties.get("status"), dict) else {}
                    status_type = str(status.get("type") or "")
                    if status_type == "busy":
                        turn_started = True
                        idle_polls = 0
                    elif status_type == "retry":
                        turn_started = True
                        idle_polls = 0
                        notice = str(status.get("message") or "").strip()
                        attempt = status.get("attempt")
                        yield {
                            "type": "status",
                            "message": f"OpenCode is retrying (attempt {attempt}). {notice}".strip(),
                        }
                    continue

                if event_type == "session.idle":
                    # Authoritative end of turn. session.error can still follow.
                    turn_ended = True
                    drain_until = loop_clock.time() + _IDLE_DRAIN_SECONDS
                    continue

                if event_type in {"session.error", "session.next.step.failed"}:
                    detail = format_opencode_error(properties.get("error")) or "OpenCode reported an error"
                    if detail not in relay_errors:
                        relay_errors.append(detail)
                    yield {"type": "error", "error": detail}
                    continue

                if event_type == "error":
                    detail = str(properties.get("message") or payload.get("message") or "OpenCode error")
                    if detail not in relay_errors:
                        relay_errors.append(detail)
                    yield {"type": "error", "error": detail}
                    continue

            outcome = TurnOutcome()
            if not cancelled:
                outcome = summarize_turn(
                    await _load_messages(client, active_session_id, directory),
                    prior_message_ids=prior_message_ids,
                )
                # Anything the client missed while streaming (a dropped SSE
                # connection, a reasoning block that only landed server-side)
                # is replayed now so the UI ends up with the whole turn.
                if outcome.response and not "".join(streamed_text).strip():
                    yield {"type": "token", "token": outcome.response}
                if outcome.reasoning and not "".join(streamed_reasoning).strip():
                    yield {"type": "reasoning_token", "token": outcome.reasoning}
                for message in outcome.errors:
                    if message not in relay_errors:
                        yield {"type": "error", "error": message}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("OpenCode relay failed for %s", target_dir)
            detail = f"OpenCode relay failed: {exc}"
            if detail not in relay_errors:
                relay_errors.append(detail)
            yield {"type": "error", "error": detail}
        finally:
            stop_reading.set()
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader

    if cancelled:
        yield {
            "type": "final",
            "message": "Run cancelled.",
            "ok": True,
            "cancelled": True,
        }
        return

    yield build_final_event(
        outcome,
        turn_ended=turn_ended,
        relay_errors=relay_errors,
        asked_user=asked_user,
        fallback_response="".join(streamed_text),
        fallback_reasoning="".join(streamed_reasoning),
    )


def _looks_like_opencode_session(session_id: str) -> bool:
    value = session_id.strip()
    return value.startswith("ses") or (len(value) >= 16 and not _looks_like_project_uuid(value))


def _looks_like_project_uuid(session_id: str) -> bool:
    return bool(re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        session_id.strip(),
    ))


def _is_missing_session_error(stderr: str) -> bool:
    text = stderr.lower()
    return "session not found" in text or "resource not found" in text
