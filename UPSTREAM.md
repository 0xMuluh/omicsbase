# Upstream provenance and customization

The authoritative source baselines and SHA-256 hashes are in `upstream/manifest.json`.

| Source checkout | Baseline commit | Source |
| --- | --- | --- |
| LibreChat | `12d78909d3f5247a8d40a47f0ef5c3ac771d9c5a` | https://github.com/danny-avila/LibreChat |
| OpenHands frontend | `a07364828c8f202e7745c6bce3dcef3915ae7ac1` | https://github.com/all-hands-ai/OpenHands |

For each checkout, `changes.patch` preserves changes to upstream-tracked files and `additions/` preserves new files. Patches include full blob IDs and binary support. Python bytecode and local runtime data are excluded. Upstream repositories remain independent local checkouts; the outer repository tracks their snapshots, not gitlinks.

## OpenHands v1.x Architecture Migration (Option B)

The legacy OpenHands 0.59.0 monolith and its runtime monkey-patch overlays (`action_execution_server.py`, `files.py`, `system_prompt.j2`, `fn_call_converter.py`, and `omicsbase_shared.runtime.UserRuntime`) have been completely retired.

OmicsBase now deploys OpenHands v1.20.0 (`@openhands/agent-canvas` + `openhands-agent-server` 1.49.x) with the following architectural properties:
1. **No Docker Socket Mounting**: The runtime executes commands directly within the hardened container sandbox rather than spinning up ephemeral sibling Docker containers via `/var/run/docker.sock`.
2. **Integrated Bioinformatics Toolchain**: The container includes system R (4.5.0), Bioconductor system dependencies, and Quarto CLI (1.6.42), allowing agents to immediately execute bioinformatics pipelines, R scripts, and Quarto document compilation.
3. **OmicsBase Gateway**: A lightweight internal FastAPI gateway (`docker/openhands/gateway/app.py`) runs inside the container on port 18002, authenticated via LibreChat JWT launch tickets (`OMICSBASE_AUTH_SECRET`). It creates scoped `LocalWorkspace` conversations pinned to `/projects/users/<user_id>/<project_id>` on the Agent Server (port 18000).
4. **Agent Canvas Reverse Proxy**: The static server routes `/api/omicsbase` requests to the gateway while serving the modern Agent Canvas UI at root path (`AGENT_CANVAS_BASE_PATH=/`) with embedded LibreChat iframe support (`?embedded=true`).

`deployment/local-image-inventory.json` records configured image names, immutable local image IDs, and available registry digests without secrets.

## LibreChat Upgrade Verification (Baseline 12d78909d)

LibreChat has been upgraded and verified against upstream HEAD `12d78909d3f5247a8d40a47f0ef5c3ac771d9c5a` (284 commits ahead of `f9f1b2fb9`):
1. **Core Package Builds**: `packages/data-provider`, `packages/data-schemas`, `packages/client`, and `packages/api` build cleanly with `tsdown` and `tsc`.
2. **Frontend Bundle**: Client production bundle built cleanly with Vite in 50s with zero bundling or type errors.
3. **Regression Tests**:
   - `packages/data-provider/src/file-config.spec.ts`: 238 passed (including .rds/.RDS MIME type detection).
   - `packages/api/src/files/context.spec.ts`: 11 passed (including workspace data attachments).
   - `packages/api/src/projects/workspace.spec.ts` & `packages/api/src/mcp/__tests__/MCPManager.test.ts`: 205 passed.
   - `api/server/services/Files/process.spec.js`: 113 passed (file upload & NoteThread bridging).
   - `client/src/utils/__tests__/documentTitle.test.ts`: passed (New Note title verification).
   - `client/src/hooks/MCP/__tests__/useMCPSelect.test.tsx`: passed.
4. **Reproducibility**: Tested with clean clone and `git apply --check`, verifying 100% clean application with zero conflicts.

## OpenHands Frontend Upgrade Verification (Baseline a07364828)

OpenHands frontend tracking has been upgraded and verified against upstream HEAD `a07364828c8f202e7745c6bce3dcef3915ae7ac1` (132 commits ahead of `fe09f319b`):
1. **Delta Isolation**: The only modification in the repository is [`src/utils/file-validation.ts`](file:///home/simple/Documents/miaverse/FOPP/OmicsBase_development_lab/omicsbase/openhands/src/utils/file-validation.ts), which adjusts `MAX_FILE_SIZE` and `MAX_TOTAL_SIZE` to 20MB for omics data file uploads.
2. **Additions**: Zero untracked or custom source additions (`addition_sha256: {}`).
3. **Patch Compatibility**: `changes.patch` matches the upstream baseline and applies with 100% clean verification (`git apply --check` return code 0).
4. **Reproducibility**: Tested with `scripts/restore_upstream.py` clone-and-patch flow across both LibreChat and OpenHands, confirming complete synchronization.

## Local review and migration status

Both the LibreChat image `omicsbase-librechat:12d78909d` and the OpenHands image `omicsbase-openhands:dev` have been built and verified locally.

The deployed OpenHands integration (`integrations/openhands/frontend.json`) has been upgraded to baseline `a07364828c8f202e7745c6bce3dcef3915ae7ac1` with verified typecheck, vitest contracts, and container build (`scripts/build_openhands.py`).

To launch the combined stack for local review:

```bash
docker --context default compose \
  --project-directory "$PWD/librechat" --env-file librechat/.env \
  -p librechat -f librechat/docker-compose.yml \
  -f librechat/docker-compose.override.yml \
  up -d --force-recreate api openhands
```

Rollback requires restoring compatible source bind mounts as well as selecting
the previous image. Do not overwrite the uncommitted upgraded snapshot or use
an unverified remote branch as a rollback source. Preserve project data and
volumes. Database backward compatibility must be assessed separately.
