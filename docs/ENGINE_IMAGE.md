# Engine image provenance — 2026-09-07

The base recipe was not lost. Both `../omicsbase2/product/backend/Dockerfile` and `../omicsbase_archived1/backend/Dockerfile` exist and are byte-for-byte identical. Their development Compose configurations name `omicsbase-backend:dev`.

Docker context `default` confirms:

- OB2 containers `product-backend-1` and `product-worker-1` use image `sha256:910fe6c6a8204bac2546572dd872960fd4339895884c263981bf1d3137e6eb08`.
- Earlier OmicsBase containers `omicsbase-backend-1` and `omicsbase-worker-1` use that same exact image. Their Compose labels retain the old pre-archive `omicsbase` directory name.
- The current `omicsbase-engine` uses `sha256:7ff6c3022772d882df9b0f3afee94306fcd75646915e2960066bf4c98e108adf` (`omicsbase3-engine:dev`).
- All 35 backend image filesystem layers are an exact prefix of the engine image’s 41 layers. The engine is a derived image, not an identical image with a different tag.

The original engine Dockerfile was recovered from `/app/engine/Dockerfile` inside that image. Requirements were recovered from `/app/engine/requirements.txt` and matched `/app/requirements.txt` byte for byte. They are restored verbatim as `engine/Dockerfile` and `engine/requirements.txt`. Extraction used a temporary container that was never started and was removed afterward. Running services were not changed.

The engine Dockerfile starts with `FROM omicsbase-backend:dev`, installs the additional Python requirements and copies the engine source. The shared backend Dockerfile starts from `bioconductor/bioconductor_docker:RELEASE_3_23` and installs system libraries, R packages, Quarto, Python dependencies and legacy application files.

To rebuild the historical arrangement, the base Dockerfile uses its product root as context (for example `omicsbase2/product`), then the engine Dockerfile uses `omicsbase/engine` as context. No image rebuild was performed during recovery.

Remaining limitations: dependency constraints and base tags are not fully pinned, the base build inputs are not yet imported into the primary repository, and image history contains two additional 59-byte layers recorded as `sleep 30` after the Dockerfile build. Their contents have not been characterized; the recovered recipe is not a claim of byte-identical reconstruction. Engine knowledge assets and project data are separate from source preservation.

Before deleting either older project, preserve the shared base recipe and all its COPY inputs in the primary build system. The OB2 source archive already preserves those source inputs separately.
