# Upstream Provenance and Submodule Architecture

OmicsBase tracks LibreChat and OpenHands customizations through **maintained GitHub forks registered as Git submodules** pinned to stable upstream release baselines (Issue #24).

The legacy snapshot/patch system (`upstream/manifest.json`, `changes.patch`, and `additions/`) has been retired. All customizations now exist as standard, reviewable Git commits on dedicated `omicsbase` branches within the maintained forks.

## Fork Provenance and Pinned Baselines

| Submodule | Maintained Fork (Tracked in `.gitmodules`) | Branch | Pinned Commit SHA | Upstream Baseline | Upstream Repository |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `librechat/` | `https://github.com/0xMuluh/LibreChat.git` | `omicsbase` | `d86005881` | `12d78909d` | https://github.com/danny-avila/LibreChat |
| `openhands/` | `https://github.com/0xMuluh/OpenHands.git` | `omicsbase` | `039938f09` | `a07364828` | https://github.com/all-hands-ai/OpenHands |

## Architectural Design

```
Upstream (danny-avila/LibreChat)
    │  (Pull only stable release tags, e.g. v0.8.x)
    ▼
Fork (0xMuluh/LibreChat : branch 'omicsbase')
    │  [First-class git commits for NoteCells, Workspace Canvas, MCP tools]
    ▼
Root Repository (0xMuluh/omicsbase)
    └─ Submodule 'librechat/' pinned to exact commit SHA on fork
```

```
Upstream (all-hands-ai/OpenHands)
    │  (Pull only stable release tags, e.g. v1.20.x)
    ▼
Fork (0xMuluh/OpenHands : branch 'omicsbase')
    │  [First-class git commits for file validation, contracts, embedded bridge]
    ▼
Root Repository (0xMuluh/omicsbase)
    └─ Submodule 'openhands/' pinned to exact commit SHA on fork
```

## Submodule Operations

### Cloning a Fresh Repository

To clone OmicsBase with all submodules populated:

```bash
git clone --recurse-submodules https://github.com/0xMuluh/omicsbase.git
```

### Initializing in an Existing Checkout

If the outer repository was cloned without submodules:

```bash
git submodule update --init --recursive
```

### Checking Submodule Status

```bash
git submodule status
python3 scripts/build_librechat.py --verify-only
python3 scripts/build_openhands.py --verify-only
```

## Upstream Maintenance Workflow

To upgrade LibreChat or OpenHands to a new upstream release:

1. **Navigate to the submodule directory**:
   ```bash
   cd librechat  # or cd openhands
   ```
2. **Fetch upstream tags and branches**:
   ```bash
   git fetch upstream --tags
   ```
3. **Create an upgrade trial branch**:
   ```bash
   git checkout -b upgrade/v0.8.x
   git merge <upstream-tag-or-commit>  # or git rebase <upstream-tag-or-commit>
   ```
4. **Resolve conflicts and verify test suites**:
   - For LibreChat:
     ```bash
     npm run build:data-provider
     npm run build:client
     npm test
     ```
   - For OpenHands:
     ```bash
     npm run make-i18n
     npm run typecheck
     npx vitest run --environment node __tests__/integrations/omicsbase.test.ts
     npm run build
     ```
5. **Fast-forward the `omicsbase` branch and push to the fork**:
   ```bash
   git checkout omicsbase
   git merge upgrade/v0.8.x
   git push origin omicsbase
   ```
6. **Bump the submodule pointer in the root OmicsBase repository**:
   ```bash
   cd ..
   git add librechat  # or git add openhands
   git commit -m "chore(submodule): bump LibreChat to v0.8.x (<commit-sha>)"
   ```

## OpenHands v1.x Architecture Migration

The legacy OpenHands 0.59.0 monolith and runtime monkey-patch overlays (`action_execution_server.py`, `files.py`, `system_prompt.j2`, `fn_call_converter.py`, and `omicsbase_shared.runtime.UserRuntime`) have been completely retired.

OmicsBase deploys OpenHands v1.20.0 (`@openhands/agent-canvas` + `openhands-agent-server` 1.49.x) with the following architectural properties:
1. **No Docker Socket Mounting**: The runtime executes commands directly within the hardened container sandbox rather than spinning up ephemeral sibling Docker containers via `/var/run/docker.sock`.
2. **Integrated Bioinformatics Toolchain**: The container includes system R (4.5.0), Bioconductor system dependencies, and Quarto CLI (1.6.42), allowing agents to immediately execute bioinformatics pipelines, R scripts, and Quarto document compilation.
3. **OmicsBase Gateway**: A lightweight internal FastAPI gateway (`docker/openhands/gateway/app.py`) runs inside the container on port 18002, authenticated via LibreChat JWT launch tickets (`OMICSBASE_AUTH_SECRET`). It creates scoped `LocalWorkspace` conversations pinned to `/projects/users/<user_id>/<project_id>` on the Agent Server (port 18000).
4. **Agent Canvas Reverse Proxy**: The static server routes `/api/omicsbase` requests to the gateway while serving the modern Agent Canvas UI at root path (`AGENT_CANVAS_BASE_PATH=/`) with embedded LibreChat iframe support (`?embedded=true`).

`deployment/local-image-inventory.json` records configured image names, immutable local image IDs, and available registry digests without secrets.

## Local Review and Verification

Both the LibreChat image `omicsbase-librechat:12d78909d` and the OpenHands image `omicsbase-openhands:dev` have been built and verified locally.

To launch the combined stack for local review:

```bash
docker --context default compose \
  --project-directory "$PWD/librechat" --env-file librechat/.env \
  -p librechat -f librechat/docker-compose.yml \
  -f librechat/docker-compose.override.yml \
  up -d --force-recreate api openhands
```
