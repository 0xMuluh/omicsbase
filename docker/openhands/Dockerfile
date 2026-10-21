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
# The base image's Debian PyYAML has no pip record, so pip cannot replace it; install
# the newer one the agent server needs alongside it first.
RUN pip install --no-cache-dir --break-system-packages --ignore-installed "PyYAML>=6.0.2" \
    && pip install --no-cache-dir --break-system-packages \
    openhands-agent-server==1.56.0 \
    openhands-tools==1.56.0 \
    openhands-automation==1.22.0 \
    fastapi uvicorn httpx pyjwt \
    && printf '#!/usr/bin/env bash\nexec python3 -m openhands.agent_server "$@"\n' > /usr/local/bin/openhands-agent-server \
    && chmod +x /usr/local/bin/openhands-agent-server

# 3. Pull in Agent Canvas assets and static server scripts from upstream image
COPY --from=ghcr.io/openhands/agent-canvas:1.29.0 /opt/agent-canvas /opt/agent-canvas

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
