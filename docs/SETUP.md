# Fresh clone setup (local development)

The repository contains the source and image build inputs. It is not a one-command packaged release. A new developer needs Git, Python 3, Docker Engine with Compose/build support, Node.js 24.16 or compatible Node 24, npm, internet access, and their own model-provider credentials. The inherited base build targets Linux amd64 and installs a substantial R/Bioconductor environment. Allow adequate disk, memory, and build time.

## Restore the customized upstream sources

From the cloned OmicsBase repository:

```bash
python3 scripts/restore_upstream.py --in-place
```

This reconstructs `librechat/` and `openhands/` at the saved upstream commits and applies the preserved changes. It refuses to overwrite existing checkouts. Nothing from OB2 or an archived directory is needed.

## Prepare local configuration

```bash
cp .env.example librechat/.env
cp deployment/librechat.yaml librechat/librechat.yaml
cp deployment/docker-compose.override.yml librechat/docker-compose.override.yml
```

Edit `librechat/.env` before starting:

- Set `UID` and `GID` to your local account values (`id -u`, `id -g`).
- Generate independent random `JWT_SECRET`, `JWT_REFRESH_SECRET`, `ADMIN_PANEL_SESSION_SECRET`, and `MEILI_MASTER_KEY` values. `openssl rand -hex 32` generates one value per invocation.
- Generate `CREDS_KEY` using `openssl rand -hex 32` and `CREDS_IV` using `openssl rand -hex 16`.
- Supply credentials/base URLs for the provider entries you intend to use in `librechat.yaml`; remove unused provider entries. The current OpenHands template defaults to the BAI provider and needs `BAI_API_KEY`. To use another provider, adapt its `LLM_MODEL`, `LLM_BASE_URL`, and `LLM_API_KEY` settings in the local override.
- For the initial local account, enable `ALLOW_REGISTRATION=true`, register through the UI, then disable registration if appropriate for your deployment.
- `OPENHANDS_STATE_DIR` can select a private persistent state directory. The current template defaults to `$HOME/.openhands`; use a dedicated directory if you already run another OpenHands instance.

Keep `.env`, provider credentials, state, databases, and project data outside Git. This setup describes localhost usage, not a production internet deployment.

## Build sources and engine images

```bash
cd librechat
npm ci
npm run frontend
cd ..
python3 scripts/build_engine.py --context default
```

The frontend command builds LibreChat's shared packages and UI. Its outputs are mounted by the deployment override. The engine command builds both analysis images from repository-local contexts.

## Start

```bash
docker --context default compose --project-directory "$PWD/librechat" --env-file librechat/.env -p librechat -f librechat/docker-compose.yml -f librechat/docker-compose.override.yml config --quiet
docker --context default compose --project-directory "$PWD/librechat" --env-file librechat/.env -p librechat -f librechat/docker-compose.yml -f librechat/docker-compose.override.yml up -d
```

Use the same explicit Docker context for builds and startup. Visit `http://localhost:3080`. Ports 3000, 3001, 3080 and 8001 must be available for this configuration. Existing containers with the same names must not belong to another deployment.

## Knowledge search and data

New clones do not contain anyone's projects, login data, or the generated `engine/knowledge/knowledge.db`. Analysis starts with new local data. The knowledge search function reports the absent index until one is provisioned; the database is not required to import the engine module or run R cells.

To build the optional index, obtain the curated book repositories listed in `engine/knowledge/indexer.py` under a directory you control, then pass explicit source and database paths:

```bash
python3 -m engine.knowledge.indexer /absolute/path/to/book-repositories "$PWD/engine/knowledge/knowledge.db"
```

The old indexer's implicit source-directory default is historical; do not rely on it. Book source acquisition and pinning are not automated yet.

## What has actually been verified

The repository-local base and engine builds passed (with available Docker cache), and the rebuilt engine passed its three real-R execution/cancellation tests. Snapshot patches were applied to their recorded Git trees and compared against the development files. The full fresh-machine sequence, including a clean LibreChat dependency install/build and new-user model conversation, has not yet been verified end to end. Treat this as developer setup instructions, not a promise of a turnkey release.
