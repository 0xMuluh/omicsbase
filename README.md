# OmicsBase

OmicsBase is an experimental workspace for conversational omics analysis. It combines LibreChat's chat interface, durable R analysis cells, project workspaces, and an embedded OpenHands coding environment with report and file-editing surfaces.

This is the primary OmicsBase implementation, previously named `omicsbase3`. The earlier `omicsbase2` product is retired and retained separately for reference.

## Development status

This repository is a source checkpoint of the existing implementation, not yet a turnkey distribution. It preserves the current customizations before architectural cleanup. Upstream upgrades require review; see [UPSTREAM.md](UPSTREAM.md).

The current deployment uses `omicsbase3-engine:dev`. Its recovered Dockerfile and requirements are in `engine/`. It extends the shared `omicsbase-backend:dev` image used by both earlier OmicsBase implementations; see [engine provenance](docs/ENGINE_IMAGE.md). The engine also uses a local knowledge database and analysis/project data that are intentionally not committed. Restoring source does not restore those assets or running sessions.

## Layout

- `engine/`: R execution, MCP/HTTP endpoints, and knowledge search/indexing.
- `upstream/`: pinned upstream commits, patches to existing files, and new integration files. Includes note cells, workspace UI, and OpenHands backend/runtime customizations.
- `deployment/`: configuration templates and a non-secret inventory of current deployment image identities.
- `scripts/`: source snapshot and restoration tools.
- `licenses/`: retained upstream notices.
- `docs/`: maintenance findings and retirement records.

The local `librechat/` and `openhands/` development checkouts are intentionally ignored by this outer repository. Their changes are captured in `upstream/`; they are not Git submodules. `projects/`, secrets, databases, dependencies, and built assets remain local.

## Preserve development changes

After editing either upstream checkout:

```bash
python3 scripts/snapshot_upstream.py
git diff --stat
git diff
```

Review and commit the snapshot along with relevant engine/configuration changes. The helper captures the current `HEAD` baseline, tracked edits, and untracked source additions. It does not alter either checkout or commit automatically. If deployment configuration changes, update the corresponding template under `deployment/` separately; never copy live secrets into this repository.

## Restore source

Python 3 and Git are required. From this repository, choose a destination that does not exist:

```bash
python3 scripts/restore_upstream.py /tmp/omicsbase-restored
```

The helper checks snapshot hashes, clones the recorded upstream repositories, checks out exact commits, applies the patches, and copies new files. It needs network access and only reconstructs the two source checkouts. It never replaces an existing checkout or starts containers.

For a complete development layout, place the restored checkouts alongside this repository's `engine/` directory. Install/build dependencies using each pinned upstream's instructions. Copy `.env.example` to `librechat/.env` and populate it locally. Copy `deployment/librechat.yaml` and `deployment/docker-compose.override.yml` into `librechat/`. The override paths are relative to that directory. Supply the engine image and required knowledge assets before starting services.

## Existing local deployment

The inspected running stack uses Docker context `default`; this machine's selected context was `desktop-linux`, which shows different containers. Use an explicit context when inspecting this deployment. Do not use this machine-specific context assumption on other hosts without checking.

The parent workspace currently has a temporary `omicsbase3 -> omicsbase` symlink because existing containers record old absolute bind-mount paths. It is outside this Git repository. Keep it until the Compose containers and persistent OpenHands user runtimes have been deliberately recreated with the new paths. See [maintenance notes](docs/MAINTENANCE.md).

## Licensing

Original OmicsBase code is available under the [MIT License](LICENSE). Third-party code retains its respective copyrights and license notices; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
