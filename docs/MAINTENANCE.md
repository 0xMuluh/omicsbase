# Maintenance and rename audit — 2026-09-07

## Preserved source

LibreChat: 47 tracked files changed (+1,333/-585 lines including the lockfile) and 37 new non-bytecode files (10,559 physical lines including inherited/vendor code). OpenHands frontend: one changed file (+4/-4). Engine: seven Python files, 1,543 lines including tests. Numbers describe the initial checkpoint; later edits can change them.

An isolated source merge against LibreChat main `b5c5624f138423d072acafe4c91d618a88b16554` found 24 overlapping changed files and six conflicts: client HTML, ToolCall, English translations, CSS, and default/dark themes. OpenHands main `f7fb0c4b21f5ed726edbba8a6309634ef434b004` merged cleanly. This was a merge simulation, not a runtime compatibility test.

## Custom responsibility hotspots

- `RCellBlock.tsx` (926 lines): bootstrap/persistence, editing, polling, execution controls/history, artifacts and rendering.
- `patches/omicsbase_shared/app.py` (674): launch, filesystem APIs, report inspection, static serving and compiled-frontend rewriting.
- `NoteCells/service.js` (652): access checks, persistence/revisions, scheduling/cancellation, events, artifacts and agent integration.
- `report.html` (1,184) and `editor.html` (902): application logic, styling and UI in single files; report also includes vendor JavaScript.

Split by responsibility after preserving behavior. Large copied upstream files are a separate upgrade burden, not original OmicsBase god files.

## Directory rename and runtime

No references to OB2 or absolute old OB3 paths were found in the inspected custom engine/integration sources. Active Docker containers on context `default` still record old `omicsbase3` bind-mount paths, including databases and a persistent OpenHands user runtime. A relative sibling symlink `omicsbase3 -> omicsbase` preserves those paths without restarting services.

Follow-up migration completed on 2026-09-07: all active Compose services and the persistent OpenHands user runtime were recreated with the new paths, preserving image versions, database/project mount destinations and named volumes. The compatibility symlink was removed after health checks. Stopped historical/rollback containers may retain old metadata, but no active service depends on the old path. See `PATH_MIGRATION.md`.

The tag `omicsbase3-engine:dev` is an existing image name, not an obsolete filesystem reference. It is retained intentionally. Its Dockerfile and requirements were subsequently recovered from the image and restored under `engine/`. Both older projects contain the identical shared backend Dockerfile. The exact backend image used by their containers is also the engine image’s base; see `ENGINE_IMAGE.md`. The base build inputs have now been imported into `docker/engine-base/`, and both images build using only repository-local contexts. The local knowledge database remains excluded; knowledge provisioning is still separate from source and image builds.

## OB2 retirement

OB2 has unique product source and 12 modified VS Code files plus one added theme file. Its containers were stopped when inspected. The directory is retained in place because legacy container mounts and old scripts refer to it. A separate local archive preserves product source, the VS Code diff, its added file, baseline metadata and checksums; see `ob2-archive.json`.

The archive excludes secrets, dependencies, builds, user/project data and the full upstream VS Code tree. These remain in the original directory. Retirement is not authorization to delete that data. Review any reusable product/UI/analysis code before eventual deletion.
