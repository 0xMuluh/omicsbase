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

## Section links and the function map

The index also holds each section's web address (`sections`) and the functions used in each book section (`function_sections`, read by the book badge on R cells). Three optional steps, run where R and network access are available:

```bash
# 1. Names exported by the installed R packages; limits the function map to real package functions.
python3 scripts/build_package_knowledge.py --exports-only      # writes engine/knowledge/package_exports.json
# 2. Move the pins in sources.json to the head of each book's branch (commit ids only, no download), then review the diff.
python3 scripts/setup_knowledge.py --update-pins
# 3. Build, checking every section link against the live page (missing anchor: chapter page; unreadable page: repository file).
python3 scripts/setup_knowledge.py --check-anchors
```

Without `package_exports.json` the map is built from every call found in book code. Per book, `sources.json` gives the page address template (`web`), the files left out because the book does not render them (`exclude`, `chapters_file`), and the branch its pin follows (`branch`; `release` means the top-level `release_branch`). Rebuilding the index removes the package help added by `scripts/build_package_knowledge.py`; run that again afterwards (`--skip-extract` reuses the extracted JSON).

Verified corpus: OSCA 22 chunks, OSTA 1,004, OMA 967, R for Mass Spectrometry 6, and Metabonaut 410: 2,409 total. A fresh download reproduced the existing local index content exactly. This describes the current QMD/Rmd extraction, not exhaustive coverage of all HTML/PDF/book subprojects.

Original source notices are preserved in downloaded repositories and referenced in `sources.json`, the SQLite metadata table, and search results. Some source terms are noncommercial or differ between package metadata and book text; see the attribution file. This setup does not change their licenses.
