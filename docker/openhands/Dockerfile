ARG OPENHANDS_BASE_IMAGE=ghcr.io/all-hands-ai/openhands@sha256:00968de77a7b36546413e1f3b5c6b4e2c387d19ade54de7926e10f5b3d265fa6
FROM ${OPENHANDS_BASE_IMAGE}
RUN rm -rf /app/frontend/build
COPY frontend/ /app/frontend/build/
COPY integration/ /app/omicsbase_shared/
