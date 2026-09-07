# Install curated knowledge

A fresh clone can reproduce the existing five-source library without OB2, the archive, or anyone's private data. Exact repository revisions and source notices are recorded in `sources.json` and [ATTRIBUTION.md](ATTRIBUTION.md).

From the repository root, using the engine image built by `scripts/build_engine.py`:

```bash
docker --context default run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD:/app" --entrypoint python3 omicsbase-engine:dev /app/scripts/setup_knowledge.py
```

Alternatively, with Python 3, Git, SQLite FTS5, and PyYAML installed:

```bash
python3 scripts/setup_knowledge.py
```

For a dedicated Python environment:

```bash
python3 -m venv .knowledge/venv
.knowledge/venv/bin/pip install -r knowledge/requirements.txt
.knowledge/venv/bin/python scripts/setup_knowledge.py
```

The command fetches exact commits from public repositories into `.knowledge/sources/`, verifies the checkout revisions, indexes QMD/Rmd files, adds provenance metadata, checks that every source contributes content, and atomically installs `engine/knowledge/knowledge.db`. Source code blocks are never executed. Existing indexes remain untouched if fetching or indexing fails. Repeated runs reuse clean cached checkouts; modified caches fail rather than silently changing the source corpus.

Use `--output /path/to/knowledge.db` for a separate index, or `--cache-dir /path/to/cache` for a different download location. Downloads and generated databases remain outside Git and Docker build contexts. The current deployment mounts the engine directory, so a database installed there is available without rebuilding the image.

Verified corpus: OSCA 22 chunks, OSTA 1,004, OMA 967, R for Mass Spectrometry 6, and Metabonaut 410: 2,409 total. A fresh download reproduced the existing local index content exactly. This describes the current QMD/Rmd extraction, not exhaustive coverage of all HTML/PDF/book subprojects.

Original source notices are preserved in downloaded repositories and referenced in `sources.json`, the SQLite metadata table, and search results. Some source terms are noncommercial or differ between package metadata and book text; see the attribution file. This setup does not change their licenses.
