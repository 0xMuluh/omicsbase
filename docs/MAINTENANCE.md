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

Keep the symlink until a planned migration recreates all affected containers and user runtimes with the new paths. Database/project data and named volumes must be preserved. Changing Compose metadata alone does not update existing containers. No containers or volumes were deleted or restarted for this checkpoint.

The tag `omicsbase3-engine:dev` is an existing image name, not an obsolete filesystem reference. It is retained intentionally. Its Dockerfile was not found in this workspace; the image exists on context `default`. Its identity is recorded, but its contents are not included in Git. The local knowledge database is also excluded. Recovering the build recipe and specifying knowledge provisioning remain prerequisites for a clean-machine deployment.

## OB2 retirement

OB2 has unique product source and 12 modified VS Code files plus one added theme file. Its containers were stopped when inspected. The directory is retained in place because legacy container mounts and old scripts refer to it. A separate local archive preserves product source, the VS Code diff, its added file, baseline metadata and checksums; see `ob2-archive.json`.

The archive excludes secrets, dependencies, builds, user/project data and the full upstream VS Code tree. These remain in the original directory. Retirement is not authorization to delete that data. Review any reusable product/UI/analysis code before eventual deletion.
