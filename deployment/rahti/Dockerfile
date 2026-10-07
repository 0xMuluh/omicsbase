# The engine image for Rahti: the same engine, made runnable as Rahti's random
# non-root user. Rahti starts containers with an arbitrary UID in group 0, so every
# path the engine or R writes to must belong to group 0 and be group-writable.
ARG ENGINE_IMAGE=omicsbase-engine:ecca5a3
FROM ${ENGINE_IMAGE}

USER root

# Note folders and R's download caches live on the persistent volume mounted at /data.
ENV HOME=/app/home \
    PROJECTS_DIR=/data/projects \
    EXPERIMENT_HUB_CACHE=/data/cache/ExperimentHub \
    ANNOTATION_HUB_CACHE=/data/cache/AnnotationHub \
    R_USER_CACHE_DIR=/data/cache/R \
    XDG_CACHE_HOME=/data/cache/xdg \
    RETICULATE_PYTHON=/usr/bin/python3

RUN mkdir -p /app/home /data/projects /data/cache \
    && chgrp -R 0 /app /data \
    && chmod -R g=u /app /data

USER 1001
