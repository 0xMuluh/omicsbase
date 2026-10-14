# OmicsBase

OmicsBase is an experimental workspace for conversational omics analysis. It combines LibreChat's chat interface, durable R analysis cells, project workspaces, and an embedded OpenHands coding environment with report and file-editing surfaces.

This is the primary OmicsBase implementation, previously named `omicsbase3`. The earlier `omicsbase2` product is retired and retained separately for reference.

## Development status

For a new clone, follow [fresh-clone setup](docs/SETUP.md). This is a developer setup, not a one-command packaged release.

Upstream integrations (LibreChat and OpenHands) are tracked as **maintained GitHub forks registered as Git submodules** (`0xMuluh/LibreChat` and `0xMuluh/OpenHands`), pinned to stable release commits on their respective `omicsbase` branches. See [UPSTREAM.md](UPSTREAM.md) for provenance, branch management, and upgrade workflows.

The engine and its shared analysis base can be built directly from this repository with `python3 scripts/build_engine.py`. The base recipe and all its source inputs are under `docker/engine-base/`; no OB2 or archived directory is required. See [engine build instructions](docs/ENGINE_IMAGE.md). The curated knowledge library is reproducible using [knowledge setup](knowledge/README.md), which downloads pinned public sources and builds the local index. Private analysis/project data and running sessions are not distributed.

## Layout

- `engine/`: R execution, MCP/HTTP endpoints, and knowledge search/indexing.
- `librechat/`: Git submodule tracking maintained fork (`0xMuluh/LibreChat:omicsbase`) with NoteCells, modular Workspace Canvas, and bioinformatics tooling.
- `openhands/`: Git submodule tracking maintained fork (`0xMuluh/OpenHands:omicsbase`) with Agent Canvas integrations and verified file validation.
- `deployment/`: configuration templates and a non-secret inventory of current deployment image identities.
- `scripts/`: container build and runtime preparation scripts.
- `docker/`: Dockerfiles and entrypoints for engine, runtime, and OpenHands gateway.
- `knowledge/`: pinned book sources, attribution, and setup instructions.
- `licenses/`: retained upstream notices.
- `docs/`: maintenance findings and architecture records.

## Setup and Submodules

Python 3.12+, Node.js 20+, Docker, and Git are required.

To clone OmicsBase with all submodules populated:

```bash
git clone --recurse-submodules https://github.com/0xMuluh/omicsbase.git
```

If already cloned without submodules:

```bash
git submodule update --init --recursive
```

To verify the submodule source trees before building:

```bash
python3 scripts/build_librechat.py --verify-only
python3 scripts/build_openhands.py --verify-only
```

For a complete development layout, install/build dependencies inside each submodule directory according to [docs/SETUP.md](docs/SETUP.md). Copy `.env.example` to `librechat/.env` and populate it locally. Copy `deployment/librechat.yaml` and `deployment/docker-compose.override.yml` into `librechat/`. The override paths are relative to that directory. Build the engine with `python3 scripts/build_engine.py` and provision required knowledge assets before starting services.

## Existing local deployment

The inspected running stack uses Docker context `default`; this machine's selected context was `desktop-linux`, which shows different containers. Use an explicit context when inspecting this deployment. Do not use this machine-specific context assumption on other hosts without checking.

The active Compose services and persistent OpenHands runtime were recreated with `omicsbase/` paths on 2026-09-07. See [migration verification](docs/PATH_MIGRATION.md).

## Licensing

Original OmicsBase code is available under the [MIT License](LICENSE). Third-party code retains its respective copyrights and license notices; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
