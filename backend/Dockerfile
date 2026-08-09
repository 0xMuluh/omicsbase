FROM bioconductor/bioconductor_docker:RELEASE_3_23

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    BIOCONDUCTOR_USE_CONTAINER_REPOSITORY=TRUE

USER root

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gdebi-core \
    git \
    python3 \
    python3-pip \
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
  && rm -rf /var/lib/apt/lists/*

ARG QUARTO_VERSION=1.6.42
RUN curl -fsSL -o /tmp/quarto.deb \
    "https://github.com/quarto-dev/quarto-cli/releases/download/v${QUARTO_VERSION}/quarto-${QUARTO_VERSION}-linux-amd64.deb" \
  && gdebi --non-interactive /tmp/quarto.deb \
  && rm /tmp/quarto.deb

WORKDIR /app

COPY backend/r-package-list.R backend/r-packages.R /tmp/
RUN R_PACKAGE_STAGE=cran Rscript /tmp/r-packages.R
RUN R_PACKAGE_STAGE=bioc Rscript /tmp/r-packages.R
RUN R_PACKAGE_STAGE=github Rscript /tmp/r-packages.R && rm /tmp/r-package-list.R /tmp/r-packages.R

COPY backend/requirements.txt /app/requirements.txt
RUN python3 -m pip install --break-system-packages -r /app/requirements.txt

COPY backend/alembic.ini /app/alembic.ini
COPY backend/alembic /app/alembic
COPY backend/app /app/app
COPY prompts /app/prompts
COPY registry /app/registry
COPY skills /app/skills
COPY templates /app/templates

ENV PROJECTS_DIR=/app/projects \
    PROMPTS_DIR=/app/prompts \
    SKILLS_DIR=/app/skills \
    REGISTRY_PATH=/app/registry/decision_points.yaml

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
