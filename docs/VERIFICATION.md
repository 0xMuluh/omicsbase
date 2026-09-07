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
