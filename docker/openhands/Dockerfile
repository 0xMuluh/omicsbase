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
    openhands-agent-server==1.62.0 \
    openhands-tools==1.62.0 \
    openhands-automation==1.32.1 \
    fastapi uvicorn httpx pyjwt \
    && printf '#!/usr/bin/env bash\nexec python3 -m openhands.agent_server "$@"\n' > /usr/local/bin/openhands-agent-server \
    && chmod +x /usr/local/bin/openhands-agent-server

# 3. Pull in Agent Canvas assets and static server scripts from upstream image
COPY --from=ghcr.io/openhands/agent-canvas:1.41.0 /opt/agent-canvas /opt/agent-canvas

# 4. Copy our customized frontend build, gateway, and entrypoint
COPY frontend/ /opt/agent-canvas/frontend/
COPY gateway/ /opt/agent-canvas/gateway/
# Patch upstream's own entrypoint rather than carrying a copy of it.
COPY patch_entrypoint.py /tmp/patch_entrypoint.py
RUN python3 /tmp/patch_entrypoint.py /opt/agent-canvas/entrypoint.sh \
    && chmod +x /opt/agent-canvas/entrypoint.sh && rm /tmp/patch_entrypoint.py

# 5. Create openhands user (UID 10001) and configure passwordless sudo
RUN id -u openhands >/dev/null 2>&1 || useradd -u 10001 -m -s /bin/bash openhands \
    && usermod -aG sudo openhands \
    && echo "openhands ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers \
    && mkdir -p /projects /home/openhands/.openhands \
    && chown -R openhands:openhands /projects /home/openhands /opt/agent-canvas

# 6. "Use my own computer" in a local install: the reports-link service runs the router
# (deployment/roihu/roihu_router.py) and dials out to users' machines with chisel 1.12.0
# (checksums of the release archive, from its checksums file, and of the binary).
COPY roihu_router.py /opt/omicsbase/roihu_router.py
RUN curl -fsSL -o /tmp/chisel.gz \
        https://github.com/jpillora/chisel/releases/download/v1.12.0/chisel_1.12.0_linux_amd64.gz \
    && echo "f3f180f1d93aa72cce4e6386f98cc06569a0146fbd65eb4423cf83e6434bcfe6  /tmp/chisel.gz" | sha256sum -c - \
    && gunzip -c /tmp/chisel.gz > /usr/local/bin/chisel \
    && echo "811362311f13ac4bdc42fe4e7650aca618a68e3a40f930de88536d18b14d2b52  /usr/local/bin/chisel" | sha256sum -c - \
    && chmod 755 /usr/local/bin/chisel /opt/omicsbase && chmod 644 /opt/omicsbase/roihu_router.py \
    && rm /tmp/chisel.gz

# No usage analytics from the Reports page or the agent server.
ENV AGENT_CANVAS_DISABLE_TELEMETRY=1 \
    VITE_DO_NOT_TRACK=1 \
    DO_NOT_TRACK=1

USER openhands
WORKDIR /opt/agent-canvas
EXPOSE 8000

ENTRYPOINT ["tini", "--", "/opt/agent-canvas/entrypoint.sh"]
