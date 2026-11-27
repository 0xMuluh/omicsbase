# The engine image: Bioconductor with the R packages notes use, Quarto, and the engine
# server. Build from the repository root (scripts/build_engine.py does the same):
#
#   docker build -t omicsbase-engine:dev engine
#
# The build needs engine/knowledge/knowledge.db, which is not in git: build it with
# scripts/setup_knowledge.py, or copy it from a published image with
# scripts/fetch_knowledge_db.py.
ARG BASE_IMAGE=bioconductor/bioconductor_docker:RELEASE_3_24
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    BIOCONDUCTOR_USE_CONTAINER_REPOSITORY=TRUE

USER root

# System libraries the R packages build against and load at run time, and the programs
# ips and phangorn call (MAFFT, IQ-TREE).
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    python3 \
    python3-venv \
    libxml2-dev \
    libssl-dev \
    libcurl4-openssl-dev \
    libgit2-dev \
    zlib1g-dev \
    libfontconfig1-dev \
    libfreetype6-dev \
    libpng-dev \
    libjpeg-dev \
    libharfbuzz-dev \
    libfribidi-dev \
    libgsl-dev \
    gfortran \
    liblapack-dev \
    libglpk-dev \
    libgdal-dev \
    libgeos-dev \
    libproj-dev \
    libudunits2-dev \
    libnetcdf-dev \
    libhdf5-dev \
    libfftw3-dev \
    libtiff-dev \
    mafft \
    iqtree \
  && rm -rf /var/lib/apt/lists/*

# R packages, in one layer per source so a failed later stage reuses the earlier ones.
COPY image/r-package-list.R image/r-packages.R /tmp/r/
RUN R_PACKAGE_STAGE=cran Rscript /tmp/r/r-packages.R
RUN R_PACKAGE_STAGE=bioc Rscript /tmp/r/r-packages.R
RUN R_PACKAGE_STAGE=github Rscript /tmp/r/r-packages.R && rm -rf /tmp/r

# Quarto renders the notes' reports (render_quarto_report).
ARG QUARTO_VERSION=1.6.42
RUN curl -fsSL -o /tmp/quarto.deb \
    "https://github.com/quarto-dev/quarto-cli/releases/download/v${QUARTO_VERSION}/quarto-${QUARTO_VERSION}-linux-amd64.deb" \
  && dpkg -i /tmp/quarto.deb \
  && rm /tmp/quarto.deb

# The engine's Python packages, in their own environment: the base image's Debian
# numpy and pandas are older than the engine needs and pip cannot replace them.
# reticulate uses the same Python, so MOFA2 finds mofapy2 there.
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m venv /opt/venv \
  && /opt/venv/bin/pip install -r /tmp/requirements.txt \
  && rm /tmp/requirements.txt
ENV PATH=/opt/venv/bin:$PATH \
    VIRTUAL_ENV=/opt/venv \
    RETICULATE_PYTHON=/opt/venv/bin/python

WORKDIR /app
COPY . /app/engine
# The book index is not in git; without it the agent cannot search the books.
RUN test -s /app/engine/knowledge/knowledge.db \
    || { echo "engine/knowledge/knowledge.db is missing: run scripts/setup_knowledge.py or scripts/fetch_knowledge_db.py first" >&2; exit 1; }

ENV PYTHONPATH=/app \
    PROJECTS_DIR=/app/projects \
    RECIPES_DIR=/app/recipes
EXPOSE 8001

CMD ["python3", "-m", "engine.server"]
