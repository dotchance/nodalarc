# Node Agent container — executes privileged netlink operations on each
# node, receives commands from Scheduler via NATS request/reply.
#
# Requires hostPID: true and privileged: true in the DaemonSet spec. Every
# kernel read and write happens in the agent's own Python process (netlink,
# /proc, init_module); the image carries no network, firewall, module or
# container runtime tools.
#
# Build context must be the repo root:
#   docker build -f services/node_agent/Dockerfile -t nodalarc/node-agent:latest .

FROM python:3.14-slim@sha256:5b3879b6f3cb77e712644d50262d05a7c146b7312d784a18eff7ff5462e77033
ARG PROJECT_VERSION=0+unknown
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.title="NodalArc Node Agent" \
      org.opencontainers.image.description="Privileged node-local kernel operation agent for NodalArc" \
      org.opencontainers.image.vendor=".chance (dotchance)" \
      org.opencontainers.image.authors=".chance (dotchance)" \
      org.opencontainers.image.source="https://github.com/dotchance/nodalarc" \
      org.opencontainers.image.url="https://nodal.asmolab.net" \
      org.opencontainers.image.documentation="https://github.com/dotchance/nodalarc" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${PROJECT_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}"
ENV NODALARC_VERSION=${PROJECT_VERSION} \
    NODALARC_BUILD_REVISION=${VCS_REF} \
    NODALARC_BUILD_DATE=${BUILD_DATE}

WORKDIR /app

# Install Python dependencies
COPY build/runtime-constraints.txt /tmp/nodalarc-runtime-constraints.txt

RUN pip install --no-cache-dir \
    --constraint /tmp/nodalarc-runtime-constraints.txt \
    "pydantic>=2.0" pyroute2 nats-py "protobuf>=6.31.1" \
    pyyaml kubernetes

# Add lib/ and services/ to Python path
ENV PYTHONPATH=/app/lib:/app/services:/app

# Copy shared library (includes proto/)
COPY lib/ lib/
# Workload adapter support declarations: session resolution reads them
# through the adapter registry. /app is already on PYTHONPATH.
COPY adapters/ adapters/

# Copy node_agent service
COPY services/node_agent/ services/node_agent/

ENTRYPOINT ["python", "-m", "node_agent"]
