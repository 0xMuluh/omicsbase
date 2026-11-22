# Build the engine from this repository

Run from the OmicsBase repository (Python 3, Docker and network access to package registries are required):

```bash
python3 scripts/build_engine.py
```

This sequentially builds `omicsbase-engine-base:dev` from `docker/engine-base/` and `omicsbase-engine:dev` from `engine/`. The latter uses the newly built base through `ENGINE_BASE_IMAGE`. All local inputs are in this repository; no archived sources, symlinks, or old application images are required. Change Docker context with `--context NAME`; the default is `default`. The inherited Quarto download targets Linux amd64.

The deployment template defaults to `omicsbase-engine:dev`, configurable with `OMICSBASE_ENGINE_IMAGE`. Building does not restart containers, overwrite deployed `omicsbase3-engine:dev`, or modify live deployment configuration.

Both images were successfully built on 2026-09-07. Existing Docker build cache was used where applicable; this was not an empty-cache dependency reproducibility test. Version ranges and moving base/package repositories still prevent a promise of byte-identical builds. Knowledge databases, credentials, and project data are intentionally provisioned separately.

## Historical provenance

The base recipe was not lost. Both `../omicsbase2/product/backend/Dockerfile` and `../omicsbase_archived1/backend/Dockerfile` exist and are byte-for-byte identical. Their development Compose configurations name `omicsbase-backend:dev`.

Docker context `default` confirms:

- OB2 containers `product-backend-1` and `product-worker-1` use image `sha256:910fe6c6a8204bac2546572dd872960fd4339895884c263981bf1d3137e6eb08`.
- Earlier OmicsBase containers `omicsbase-backend-1` and `omicsbase-worker-1` use that same exact image. Their Compose labels retain the old pre-archive `omicsbase` directory name.
- The current `omicsbase-engine` uses `sha256:7ff6c3022772d882df9b0f3afee94306fcd75646915e2960066bf4c98e108adf` (`omicsbase3-engine:dev`).
- All 35 backend image filesystem layers are an exact prefix of the engine image’s 41 layers. The engine is a derived image, not an identical image with a different tag.

The original engine Dockerfile was recovered from `/app/engine/Dockerfile` inside that image. Requirements were recovered from `/app/engine/requirements.txt` and matched `/app/requirements.txt` byte for byte. They were restored verbatim initially; the Dockerfile now accepts `ENGINE_BASE_IMAGE`, defaulting to the repository-built `omicsbase-engine-base:dev`. Requirements remain unchanged. Extraction used a temporary container that was never started and was removed afterward. Running services were not changed.

The recovered engine Dockerfile originally started with `FROM omicsbase-backend:dev`, installs the additional Python requirements and copies the engine source. The shared backend Dockerfile starts from `bioconductor/bioconductor_docker:RELEASE_3_23` and installs system libraries, R packages, Quarto, Python dependencies and legacy application files.

To rebuild the historical arrangement, the base Dockerfile uses its product root as context (for example `omicsbase2/product`), then the engine Dockerfile uses `omicsbase/engine` as context. No image rebuild was performed during recovery.

Remaining limitations: dependency constraints and base tags are not fully pinned, image history contains two additional 59-byte layers recorded as `sleep 30` after the Dockerfile build. Their contents have not been characterized; the recovered recipe is not a claim of byte-identical reconstruction. Engine knowledge assets and project data are separate from source preservation.

The shared base recipe and all 299 source inputs are now imported into `docker/engine-base/`, with original SHA-256 hashes recorded in `provenance.json`. Neither older directory is needed to build these images. The inherited application source is retained to preserve the base build faithfully; reducing it to only engine dependencies is a separate refactor.
