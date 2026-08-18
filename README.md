# OmicsBase

OmicsBase-powered omics analysis with transparent, reproducible Quarto reports.

OmicsBase lets you:

- Specify an analysis in natural language and upload your study data
- Sit back while an agent plans the workflow, writes R + Quarto source, and renders an HTML report
- Ask the agent to improve the analysis, tweak recipes, or fix the report
- Work in NoteThreads: literate notebooks where the agent runs R cells for you, backed by a Bioconductor book knowledge base
- Adapt a versioned team ReportPack (an existing R/Quarto directory) rather than generating every report from an empty scaffold

## Getting started

### Prerequisites

- Docker (with Compose)
- Node.js 20+ and npm

### Install / run

```bash
cd omicsbase                      # repository root
cp .env.example .env              # add your LLM API key
make dev
```

- Frontend: http://localhost:3000
- API: http://localhost:8000
- API docs: http://localhost:8000/docs

`make dev` runs the backend, worker, Postgres, and Redis in Docker, and the Next.js frontend locally. See [DOCKER.md](DOCKER.md) for details and other run modes.

```bash
make down          # stop containers
```

### Setup API key

Choose one of:

- Put your key in `.env` (copy from `.env.example`)
- Export an env variable before starting

Default provider uses `ANTHROPIC_API_KEY`. You can also set `LLM_PROVIDER` / `LLM_MODEL`.

Optional:

| Variable | Purpose |
|----------|---------|
| `API_KEY` | Shared-deployment auth (`X-API-Key` header) |
| `USE_DOCKER_SANDBOX` | Run R/Quarto in an isolated container (requires `omicsbase-runner` image) |
| `REPORT_PACKS_DIR` | Optional administrator-managed directory of additional ReportPacks |

## Create a new analysis

1. Open the app and describe your research question
2. Attach data (CSV, TSV, Excel, BIOM, QIIME2 `.qza`, RDS, and more)
3. Choose **Build** (run through) or **Plan** (review the workflow first)
4. Open the workspace for the report preview, source files, and chat

## Improve an existing project

In the workspace, ask for changes in natural language, for example:

- "Enable PERMANOVA and re-run"
- "Use Shannon for alpha diversity"
- "What does the beta diversity page show?"
- "Fix the bar plot"

You can also edit files in the Code panel, save, and re-render.

## What it produces

- A structured analysis plan bound to analysis recipes
- An R + Quarto project (plan, source, data copies)
- A rendered HTML report plus machine-readable results
- A downloadable project zip

Post-render review checks the selected ReportPack's declared sources and artifacts, QMD pages, `sessionInfo`, and portable paths. Available packs are listed at `GET /api/report-packs`; plans select them by ID rather than raw filesystem path.

## NoteThreads

- Ask questions or start analyses in a notebook-style thread; the agent runs R cells with a shared workspace
- Cells produce inline tables, plots, and output files you can inspect and download
- A curated Bioconductor book knowledge base (synced at runtime from public sources) grounds the agent's answers

## Architecture

| Layer | Stack |
|-------|-------|
| Frontend | Next.js, React, Monaco, TanStack Query |
| Backend | FastAPI, SQLAlchemy, Celery, Redis, PostgreSQL |
| Analysis | R, Bioconductor, Quarto (Docker) |

One agent loop owns a run end to end: it plans (`set_plan`), stages a ReportPack, adapts source, runs R, renders, reads its own failures, and repairs — all with inline tools in a single conversation. Pipeline endpoints (`/plan`, `/generate`, `/run`, `/edit`) are thin adapters that start the same loop headlessly; deterministic checks (validation, budgets, hash-checked edit journal) live below the model as tools, not above it as gates.
