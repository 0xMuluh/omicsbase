# Reports image for per-user jobs on Roihu: the Reports image plus the tunnel client, the
# current OmicsBase gateway, and upstream's entrypoint adapted for a shared compute node
# (roihu_entrypoint.py). Our own patched entrypoint stays for the VM; Roihu jobs start
# entrypoint.roihu.sh. Built from the repository root:
#
#   docker build -f deployment/roihu/Dockerfile -t ghcr.io/0xmuluh/omicsbase-reports:<tag> .
ARG BASE_IMAGE=omicsbase-openhands:v1.41.0
ARG CANVAS_IMAGE=ghcr.io/openhands/agent-canvas:1.41.0
FROM ${CANVAS_IMAGE} AS canvas

FROM ${BASE_IMAGE}
USER root

# Current OmicsBase OpenHands frontend (openhands/, built with scripts/build_openhands.py)
# and gateway.
RUN rm -rf /opt/agent-canvas/frontend /opt/agent-canvas/gateway
COPY openhands/build/ /opt/agent-canvas/frontend/
COPY docker/openhands/gateway/ /opt/agent-canvas/gateway/
COPY --from=canvas /opt/agent-canvas/entrypoint.sh /tmp/entrypoint/upstream.sh
COPY docker/openhands/patch_entrypoint.py deployment/roihu/roihu_entrypoint.py /tmp/entrypoint/
RUN python3 /tmp/entrypoint/roihu_entrypoint.py /tmp/entrypoint/upstream.sh /opt/agent-canvas/entrypoint.roihu.sh \
    && chmod 755 /opt/agent-canvas/entrypoint.roihu.sh \
    && rm -r /tmp/entrypoint
# One user per job: every gateway request must carry the job's session key.
ENV OMICSBASE_GATEWAY_MODE=roihu

# chisel 1.12.0: checksum of the release archive (from the release's checksums file) and
# of the binary itself.
RUN curl -fsSL -o /tmp/chisel.gz \
        https://github.com/jpillora/chisel/releases/download/v1.12.0/chisel_1.12.0_linux_amd64.gz \
    && echo "f3f180f1d93aa72cce4e6386f98cc06569a0146fbd65eb4423cf83e6434bcfe6  /tmp/chisel.gz" | sha256sum -c - \
    && gunzip -c /tmp/chisel.gz > /usr/local/bin/chisel \
    && echo "811362311f13ac4bdc42fe4e7650aca618a68e3a40f930de88536d18b14d2b52  /usr/local/bin/chisel" | sha256sum -c - \
    && chmod 755 /usr/local/bin/chisel \
    && rm /tmp/chisel.gz

USER openhands
