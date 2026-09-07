# Upstream provenance and customization

The authoritative source baselines and SHA-256 hashes are in `upstream/manifest.json`.

| Source checkout | Baseline commit | Source |
| --- | --- | --- |
| LibreChat | `f9f1b2fb951a99e1fd01ee7291304a0370ea6132` | https://github.com/danny-avila/LibreChat |
| OpenHands frontend | `fe09f319b0e66dbbcd2779e6b44c928d8516b44d` | https://github.com/all-hands-ai/OpenHands |

For each checkout, `changes.patch` preserves changes to upstream-tracked files and `additions/` preserves new files. Patches include full blob IDs and binary support. Python bytecode and local runtime data are excluded. Upstream repositories remain independent local checkouts; the outer repository tracks their snapshots, not gitlinks.

## Deployment is a separate version boundary

The current OpenHands deployment uses a pinned image plus a 0.59.0 runtime image. It does not build or run directly from the current OpenHands frontend checkout. Its backend overlays are stored under `upstream/librechat/additions/patches/`, reflecting their current location in the development tree.

The copied `action_execution_server.py`, `files.py`, `fn_call_converter.py`, and `system_prompt.j2` closely match OpenHands release `0.59.0`. Comparison against that tag found respectively +32/-4, +17/-11, +4/-0, and +47/-22 lines. This is a release-tag comparison, not proof of identity with the exact deployed image.

`deployment/local-image-inventory.json` records configured image names, immutable local image IDs, and available registry digests without secrets. The Compose base uses moving LibreChat image tags; this checkpoint records them but does not change the running deployment.

## Upgrade policy

1. Commit the latest snapshot before changing a baseline.
2. Trial the new upstream version in an isolated checkout.
3. Review conflicts and semantic changes; a clean textual merge is insufficient.
4. Build/typecheck affected workspaces and exercise notes, uploads, R execution, cancellation, workspace launch, identity separation, reports, and embedded UI.
5. Update the manifest by rerunning `scripts/snapshot_upstream.py` only once the local baseline and customizations are intentional.

Priorities: eliminate minified-JavaScript replacement; isolate branding/theme edits; replace full copied backend modules with small versioned patches or supported extension points; bring the shared backend base recipe and its inputs into the primary repository before removing the retired projects.
