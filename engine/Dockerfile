ARG ENGINE_BASE_IMAGE=omicsbase-engine-base:dev
FROM ${ENGINE_BASE_IMAGE}

USER root
WORKDIR /app

COPY requirements.txt /app/requirements.txt
# Debian-installed Python packages (e.g. numpy) have no pip record and cannot be
# uninstalled, so install the requirements alongside them.
RUN pip install --break-system-packages --ignore-installed -r /app/requirements.txt

COPY . /app/engine

ENV PYTHONPATH=/app
ENV PROJECTS_DIR=/app/projects
ENV RECIPES_DIR=/app/recipes
EXPOSE 8001

CMD ["python3", "-m", "engine.server"]
