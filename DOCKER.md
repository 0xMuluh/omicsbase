# Docker Runtime

OmicsBase's recommended development path runs the backend, worker, Postgres, and Redis in Docker, while the frontend runs locally for fast Next.js hot reload.

```bash
make dev
```

This is the path to use for normal work.

## Why Docker is the default

The analysis runtime depends on R, Quarto, CRAN packages, and Bioconductor packages. Local R installs are fragile and slow to repair. The backend image therefore starts from:

```Dockerfile
bioconductor/bioconductor_docker:RELEASE_3_22
```

That release is aligned with R 4.5 and provides the Bioconductor-oriented system dependency base we need for packages such as `ANCOMBC`, `DESeq2`, `phyloseq`, and `TreeSummarizedExperiment`.

The image then layers:

- Quarto 1.6.42
- Python backend dependencies from `backend/requirements.txt`
- Standard CRAN packages from `backend/r-package-list.R`
- Standard Bioconductor packages from `backend/r-package-list.R`
- Backend app code, prompts, registry, and migrations

## Commands

### Recommended development

```bash
make dev
```

Starts Docker backend services in the background, then starts the local frontend at `http://localhost:3000`.

### Docker backend only

```bash
make dev-docker
```

Runs backend and worker in Docker in the foreground. Useful when you want container logs directly.

### Build the Docker image

```bash
make docker-build
```

Rebuilds the backend/worker image after dependency or Dockerfile changes.

### Local backend path

```bash
make dev-local
```

Runs backend and worker on the host. This uses your host R library and requires:

```bash
make r-deps
```

Use this only when you specifically need local Python/R debugging.

## Docker access

If Docker is installed but `make dev` says it is not accessible, the current shell probably has stale group membership.

Try:

```bash
newgrp docker
make dev
```

One-shot workaround:

```bash
sg docker -c 'make dev'
```

If your user is not in the Docker group yet:

```bash
sudo usermod -aG docker "$USER"
```

Then log out and back in.

## Adding R packages

Add durable render dependencies to `backend/r-package-list.R`, then rebuild:

```bash
make docker-build
```

Do not rely on generated reports calling `install.packages()` at render time. Runtime installs are slower, less reproducible, and can fail when system libraries or network access differ.
