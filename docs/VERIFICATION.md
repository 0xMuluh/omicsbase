# Checkpoint verification — 2026-09-07

- Both captured patches apply to their recorded upstream commit using an isolated Git index.
- Reconstructed tracked file contents match the development checkouts byte for byte.
- All 37 captured new files match the source and manifest SHA-256 hashes.
- Snapshot/restore helpers compile successfully; restoration rejects an existing destination before any modification. The network clone restoration path has not been run end to end.
- Known non-placeholder secret values from the local LibreChat `.env` were searched for in proposed source files; none were found. This is a targeted scan, not a claim of exhaustive secret detection.
- OB2 archive checksums and all 412 archive members verified; credentials and runtime data excluded.
- Compatibility symlink resolves to the renamed primary directory. Inspected LibreChat, engine and OpenHands server bind sources all exist.
- HTTP GET `/` returned 200 from LibreChat (3080) and OpenHands (3001). Engine (8001) returned 404 at `/`, confirming an HTTP listener but not an execution health test.
- No analysis execution, model calls, container restarts, database changes, or upstream merges were performed.
- Existing whitespace at two patch locations is preserved intentionally in this faithful source checkpoint.

## Repository-owned engine build

- Imported all 299 base source files with matching SHA-256 hashes and verified every Docker COPY source exists inside the repository.
- Built `omicsbase-engine-base:dev` and `omicsbase-engine:dev` using only `docker/engine-base/` and `engine/` build contexts; cached layers were used where available.
- Ran all three existing engine tests against the rebuilt image in a temporary read-only container with no host mounts or external network. Real R execution, persistence failure handling, errors, and cancellation passed. Existing kernel cleanup ResourceWarnings were emitted; they did not fail the tests.
- Running deployment images and containers were not replaced.

## Follow-up path migration and setup

The active Compose services and persistent OpenHands runtime were recreated with canonical paths. Mount identities and live image versions were preserved; database and HTTP/MCP/runtime health checks passed. The compatibility symlink was removed and web/MCP checks repeated successfully. See `PATH_MIGRATION.md`.

`restore_upstream.py --in-place` now supports a fresh repository clone and refuses existing checkouts before modifying anything. End-to-end clean-machine setup and model-provider validation remain unverified; `SETUP.md` documents the required manual configuration.

## Reproducible curated knowledge

Fetched all five pinned public source repositories over the network into a new repository-local cache. The generated index contains 2,409 chunks and its indexed content matches the previous local library exactly. Source commits, notice links, and attribution are embedded in the database and exposed by search results. The standard local database was then installed atomically using the verified cache.

Installer tests cover actual local Git fetches, complete book coverage, repeated installation, attribution, rejection of a modified cache, and preservation of the previous database on indexing failure. Both tests pass. No book source chunks are evaluated. Source licenses are recorded independently of OmicsBase's MIT license.
