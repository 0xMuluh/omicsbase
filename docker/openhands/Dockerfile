ARG BASE_IMAGE=omicsbase-engine:dev
FROM ${BASE_IMAGE}

USER root

# 1. Install Node 24 (required by Agent Canvas static server), tini, and tmux
RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y --no-install-recommends \
        nodejs \
        tini \
        tmux \
    && rm -rf /var/lib/apt/lists/*

# 2. Install OpenHands Agent Server, Automation, Tools, and Gateway dependencies
RUN pip install --no-cache-dir --break-system-packages \
    openhands-agent-server==1.49.2 \
    openhands-tools==1.49.2 \
    openhands-automation==1.13.2 \
    fastapi uvicorn httpx pyjwt \
    && printf '#!/usr/bin/env bash\nexec python3 -m openhands.agent_server "$@"\n' > /usr/local/bin/openhands-agent-server \
    && chmod +x /usr/local/bin/openhands-agent-server \
    && python3 -c 'p = "/usr/local/lib/python3.12/dist-packages/openhands/sdk/llm/utils/telemetry.py"; s = open(p).read(); old = "details.cache_creation_tokens\n                if \"cache_creation_tokens\" in details.model_fields_set\n                else 0"; new = "getattr(details, \"cache_creation_tokens\", None) or getattr(details, \"cache_write_tokens\", None) or (details.get(\"cache_creation_tokens\") if isinstance(details, dict) else 0) or 0"; assert old in s, "Telemetry patch target not found"; open(p, "w").write(s.replace(old, new, 1))'

# 3. Pull in Agent Canvas assets and static server scripts from upstream image
COPY --from=ghcr.io/openhands/agent-canvas:1.20.0 /opt/agent-canvas /opt/agent-canvas

# 4. Copy our customized frontend build, gateway, and entrypoint
COPY frontend/ /opt/agent-canvas/frontend/
COPY gateway/ /opt/agent-canvas/gateway/
COPY entrypoint.sh /opt/agent-canvas/entrypoint.sh
RUN chmod +x /opt/agent-canvas/entrypoint.sh

# 5. Create openhands user (UID 10001) and configure passwordless sudo
RUN id -u openhands >/dev/null 2>&1 || useradd -u 10001 -m -s /bin/bash openhands \
    && usermod -aG sudo openhands \
    && echo "openhands ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers \
    && mkdir -p /projects /home/openhands/.openhands \
    && chown -R openhands:openhands /projects /home/openhands /opt/agent-canvas

USER openhands
WORKDIR /opt/agent-canvas
EXPOSE 8000

ENTRYPOINT ["tini", "--", "/opt/agent-canvas/entrypoint.sh"]
