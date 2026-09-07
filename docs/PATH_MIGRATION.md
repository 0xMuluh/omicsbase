# Directory-path migration — 2026-09-07

All eight active `librechat` Compose services and the active persistent OpenHands user runtime were recreated using the canonical `omicsbase/` bind-source paths. The old `omicsbase3` compatibility symlink was removed afterward.

The Compose project name and exact deployed image IDs were retained. Every before/after mount destination and named-volume identity was compared; bind sources differ only by the intended directory rename. The runtime's writable filesystem was preserved in a local snapshot image before recreation; its ports and authentication configuration were retained. Runtime processes restarted, so in-memory session state is not guaranteed to survive. Project files and database volumes were not deleted.

Private rollback inspection metadata and the temporary image override are at `/tmp/omicsbase-migration-wjmox8tn` on the original host (not included in Git). The old runtime is retained as a stopped rollback container with automatic restart disabled. Historical stopped containers may retain old-path metadata and should not be started without reviewing their mounts. These are not active application dependencies.

Verified after recreation:

- LibreChat root: HTTP 200.
- OpenHands root: HTTP 200.
- Engine MCP `/sse`: HTTP 200.
- Persistent runtime `/alive`: HTTP 200 using its existing session authentication.
- MongoDB admin ping: success.
- PostgreSQL readiness: accepting connections.
- Admin panel: Docker health status healthy.
- No active container bind source contains the old `omicsbase3` directory path.
- Web and MCP checks repeated successfully after symlink removal.

This migration preserved the existing deployed versions. The separately built `omicsbase-engine:dev` remains available; the fresh-clone deployment template uses that repository-built image. The original live engine tag is still `omicsbase3-engine:dev`; an image tag containing the old name is not a filesystem dependency.
