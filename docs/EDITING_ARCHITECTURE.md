# OmicsBase editing architecture and rollout plan

## Decision

OmicsBase should remain adaptive at the model boundary. The model may choose SEARCH/REPLACE, an embedded patch envelope, or a justified full-file rewrite; the server owns safety, applicability, provenance, and commit semantics. “Deterministic” therefore means deterministic enforcement and recovery, not a fixed prompt or fixed edit format.

Every interactive source edit (agent, browser, inline AI, repair, or notebook promotion) follows the same pipeline. Generator-owned scaffolding and spawning retain their content-addressed generation checkpoint; generated adaptations enter this edit boundary:

```text
model proposal
  -> schema/capability gate
  -> path, extension, lock, size and base-hash preflight
  -> virtual-tree application of every operation
  -> conservative unique matching / patch validation
  -> human-visible diff and transaction journal
  -> execution-lock + hash recheck
  -> staged atomic commit
  -> render/QA feedback and durable action record
```

A failed operation aborts the transaction. A later retry sees the unchanged source tree and receives structured diagnostics.

## Supported edit representations

- **SEARCH/REPLACE:** preferred for small changes. Exact matching is tried first, then Unicode punctuation/newline normalization, uniform indentation, and an explicit single-line `...` elision anchor. Ambiguous or zero matches fail; similarity and cross-file candidates are diagnostic only.
- **Patch envelope:** `*** Begin Patch` with embedded `*** Add/Update/Delete File` paths. Update hunks are applied against unique context in memory. The envelope can cover multiple files without an outer path.
- **Full content:** allowed for new files and complete, justified rewrites. Existing files require a base SHA-256; editor/repair adapters reject rewrites when the model only saw a truncated excerpt.

## Transaction and recovery contract

`backend/app/services/edit_engine.py` is the single write boundary for interactive/adaptive edits. It provides:

- a project-relative path jail with symlink containment and extension policy;
- dynamic `locks.json` checks both before preparation and under the commit lock;
- optimistic base hashes (CAS) for rewrites, browser saves, inline edits, and note promotion;
- virtual-tree preparation so multi-file failures cannot partially apply;
- the same `.omicsbase/execution.lock` used by rendering;
- staged temporary writes, fsync, atomic replacement, rollback, and before/after copies;
- a manifest with transaction origin, summary, timestamp, hashes, strategies, reasons, validation diagnostics, and diff metadata;
- an indexed `ProjectEdit` database record synchronized from the journal, so history survives API/process restarts;
- hash-guarded revert: Undo refuses to overwrite a later edit.

The journal API is `/api/projects/{project_id}/edits`; the workspace History panel exposes committed transactions and Undo.

## Scientific and notebook boundaries

- ReportPack generation remains adaptive: pack prompt references, complete source coverage, explicit no-change/delete decisions, adaptation evidence, and execution contracts are retained.
- Legacy recipe tools are advertised only when `code/study_config.yml` is materialized; ReportPack projects do not receive semantically disconnected recipe actions.
- `promote_to_workspace` accepts `cell_id`, immutable `revision_id`, and successful `execution_id`; the execution fingerprint must match the revision. Model-supplied raw content is not trusted. Promotion uses the edit journal and records provenance.
- Standalone NoteThreads carry their question, cells, successful execution previews, uploads, and manifest into a new workspace instead of creating an empty shell; the UI presents a transfer review and defaults auto-build off.

## Tool choices

Adopted:

1. Aider’s compact SEARCH/REPLACE ergonomics and bounded reflection.
2. Codex/OpenCode-style embedded patch markers for multi-file edits.
3. Codebuff-style proposal/commit separation and diff-before-write behavior.
4. OpenCode-style diagnostics and permission/CAS concepts, with OmicsBase’s scientific locks and execution contract.
5. Aider/OpenCode-style validation feedback, implemented through the existing render, QA, and repair paths.

Deliberately not adopted:

- low-threshold Levenshtein auto-apply; it can silently target the wrong scientific code;
- cross-file remapping after a path mismatch; it hides model errors;
- raw whole-file writes from truncated context;
- unconditional Git commits; users may bring their own VCS policy.

## Rollout status

### Shipped in this tranche

- Declarative ToolSpec registry for Workspace and Note lenses (strict schemas, risk/state/capability/intent/parallel/idempotency metadata), capability-gated advertisement, and a hidden render/repair compatibility alias.
- Shared transactional edit engine and tests.
- Editor, automatic repair, workspace inline edits, browser Monaco saves, inline-AI scoping, generator adaptation, and note promotion routed through the engine.
- ETag/If-Match browser CAS with 409 conflict and 428 missing-precondition responses.
- Durable filesystem edit journal plus ProjectEdit DB index/migration, bounded diffs, safe Undo API, selectable workspace diff history, stale-save conflict dialog, and HTTP 423 busy-lock responses.
- Structured UTF-8/NUL/YAML/Quarto-frontmatter/R-source validation before high-impact commits; capability validator preflight; and persisted ReportPack-aware invalidation metadata that records impacted capabilities, resumes from the earliest affected step, or renders only changed QMD pages (legacy execution-contract fallback retained).
- Recipe capability gate, note provenance gate, journaled NoteThread report export and standalone-note transfer review, project-name ownership/CAS, resumable generation checkpoints, quota-aware provider blocking, and duplicate non-idempotent call suppression.

### Next hardening phases

1. **Patch grammar and parser evaluation:** add golden fixtures for multi-hunk insertions, no-final-newline files, add/delete, and conflict diagnostics; fuzz the parser and matcher.
2. **Scientific execution depth:** run declared validator steps in the execution contract with explicit result provenance, add Quarto-aware semantic checks, and publish golden microbiome/metabolomics studies.
3. **Review workflow:** add an explicit prepare/diff approval mode for high-impact or multi-file changes; low-risk targeted edits can continue to use the fast preview/save path.
4. **Capability catalog:** extend capability declarations beyond the built-in exemplars, validate required parameter bindings in plan review, and expose capability/validator status in the UI.
5. **Observability/evaluation:** measure first-pass apply rate, ambiguous-match rate, conflict rate, rollback rate, render pass rate, scientific-validator pass rate, quota-stop rate, and cost per accepted change.
6. **Operational recovery:** add startup journal scanning, an admin repair command for `committing`/`rolled_back` journals, PostgreSQL concurrency tests, and retention limits for before/after copies.

## Acceptance criteria

A release is ready when a model can change a generated report without relying on fixed inputs, while: (a) no failed batch leaves bytes behind, (b) stale browser or concurrent edits return a conflict, (c) every accepted change has a diff, hashes, origin, and provenance, (d) an Undo cannot clobber later user work, (e) a quota failure resumes from completed units, and (f) scientific validators and rendered artifacts remain truthful.
