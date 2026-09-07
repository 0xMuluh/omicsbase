# Maintenance and rename audit — 2026-09-07

## Preserved source

LibreChat: 47 tracked files changed (+1,333/-585 lines including the lockfile) and 37 new non-bytecode files (10,559 physical lines including inherited/vendor code). OpenHands frontend: one changed file (+4/-4). Engine: seven Python files, 1,543 lines including tests. Numbers describe the initial checkpoint; later edits can change them.

An isolated source merge against LibreChat main `b5c5624f138423d072acafe4c91d618a88b16554` found 24 overlapping changed files and six conflicts: client HTML, ToolCall, English translations, CSS, and default/dark themes. OpenHands main `f7fb0c4b21f5ed726edbba8a6309634ef434b004` merged cleanly. This was a merge simulation, not a runtime compatibility test.

## Custom responsibility hotspots

- `RCellBlock.tsx` (926 lines): bootstrap/persistence, editing, polling, execution controls/history, artifacts and rendering.
- `patches/omicsbase_shared/app.py` (674): launch, filesystem APIs, report inspection, static serving and compiled-frontend rewriting.
- `NoteCells/service.js` (652): access checks, persistence/revisions, scheduling/cancellation, events, artifacts and agent integration.
- `report.html` (1,184) and `editor.html` (902): application logic, styling and UI in single files; report also includes vendor JavaScript.

These were the initial hotspots. They have now been split by responsibility; see the implemented changes below. Large copied upstream files remain a separate upgrade burden.

## Directory rename and runtime

No references to OB2 or absolute old OB3 paths were found in the inspected custom engine/integration sources. Active Docker containers on context `default` still record old `omicsbase3` bind-mount paths, including databases and a persistent OpenHands user runtime. A relative sibling symlink `omicsbase3 -> omicsbase` preserves those paths without restarting services.

Follow-up migration completed on 2026-09-07: all active Compose services and the persistent OpenHands user runtime were recreated with the new paths, preserving image versions, database/project mount destinations and named volumes. The compatibility symlink was removed after health checks. Stopped historical/rollback containers may retain old metadata, but no active service depends on the old path. See `PATH_MIGRATION.md`.

The tag `omicsbase3-engine:dev` is an existing image name, not an obsolete filesystem reference. It is retained intentionally. Its Dockerfile and requirements were subsequently recovered from the image and restored under `engine/`. Both older projects contain the identical shared backend Dockerfile. The exact backend image used by their containers is also the engine image’s base; see `ENGINE_IMAGE.md`. The base build inputs have now been imported into `docker/engine-base/`, and both images build using only repository-local contexts. The local knowledge database remains excluded; knowledge provisioning is still separate from source and image builds.

## OB2 retirement

OB2 has unique product source and 12 modified VS Code files plus one added theme file. Its containers were stopped when inspected. The directory is retained in place because legacy container mounts and old scripts refer to it. A separate local archive preserves product source, the VS Code diff, its added file, baseline metadata and checksums; see `ob2-archive.json`.

The archive excludes secrets, dependencies, builds, user/project data and the full upstream VS Code tree. These remain in the original directory. Retirement is not authorization to delete that data. Review any reusable product/UI/analysis code before eventual deletion.

## Implemented maintenance refactor

- `RCellBlock.tsx` is now a one-line compatibility export. `components/Notes/` owns the R-cell view, lifecycle hook, output rendering, parsing, status and tool-call adapter.
- `NoteCells/service.js` is a thin dependency adapter. Typed `packages/api/src/notes/` modules own cells/revisions, execution scheduling, cancellation, events, serialization and worker processing. MCP thread binding lives in the same feature directory.
- `omicsbase_shared/app.py` only composes routers and initializes the frontend integration. Workspace paths, launch, editor, reports, report inspection and asset serving have separate modules. Editor/report CSS and JavaScript are separate files, with report syntax-highlighting vendor code separated.
- Upstream light/dark theme files are restored. OmicsBase color overrides extend them in `themes/omicsbase.ts`; the theme registry selects those overrides.
- OpenHands compiled-bundle search/replace is removed. `integrations/openhands/frontend.json` pins the deployed 0.59.0 source and checksums its source patch. `scripts/build_openhands.py` checks the patch, typechecks, tests and builds before packaging `omicsbase-openhands:dev`. Startup checks the build marker and fails explicitly for an incompatible frontend.

### Updating upstream

Keep feature logic in the OmicsBase modules and upstream changes limited to imports, adapters and configuration. Run `python3 scripts/snapshot_upstream.py` after editing reconstructed checkouts; only this snapshot is distributed by the outer repository. Review its diff before committing.

For LibreChat upgrades, apply the saved patch to the proposed upstream commit in a separate checkout, resolve remaining host-file changes, then run workspace typechecks, the note-service/agent tests and the frontend build before updating the saved baseline. Do not run an unchecked pull on the running deployment.

For the deployed OpenHands version, update the backend digest in `docker/openhands/Dockerfile` and matching source commit in `integrations/openhands/frontend.json` together. Rebase `frontend.patch`, update its SHA-256, and rebuild with `scripts/build_openhands.py`. Run the Python module/identity and HTTP-auth integration tests against that image before recreating the OpenHands service. The separate `upstream/openhands` checkout snapshot is not the source of the deployed 0.59.0 backend.

This reduces conflict surface and makes failures visible during builds; it does not guarantee conflict-free upstream upgrades. Branding CSS, translations, host UI hooks and copied backend patches still require review when upstream changes those areas. A clean source merge also cannot prove runtime API compatibility.

### Final verification

LibreChat API and UI builds and all three changed-workspace typechecks passed; the note-service/agent suites passed all nine tests. The OpenHands image built successfully after its frontend typecheck and three contract tests; five Python module/identity tests and isolated HTTP authentication checks passed against the built image.

Repeating the merge simulation against the same LibreChat target still produces six conflicting files: `client/index.html`, the host `ToolCall.tsx`, English translations, `client/src/style.css`, and the theme registry/barrel. The two theme-definition conflicts are gone, but their small selection imports now need reconciliation. The count therefore did not decrease; the custom feature logic is isolated and the remaining integration edits are more reviewable. This is not yet a conflict-free fork.

The local OpenHands service was recreated with the built image and LibreChat was restarted with rebuilt packages. HTTP readiness and the OpenHands theme bridge were checked. The persistent user runtime was left running. A full browser-driven model conversation is still outside this verification.
