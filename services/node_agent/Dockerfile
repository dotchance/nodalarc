# Node Agent container — executes privileged netlink operations on each
# K3s node, receives commands from Scheduler via NATS request/reply.
#
# Requires hostPID: true and privileged: true in the DaemonSet spec.
# Needs iproute2 for tc commands and crictl for container PID discovery.
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
      org.opencontainers.image.licenses="NodalArc Source Available License 1.0" \
      org.opencontainers.image.version="${PROJECT_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}"
ENV NODALARC_VERSION=${PROJECT_VERSION} \
    NODALARC_BUILD_REVISION=${VCS_REF} \
    NODALARC_BUILD_DATE=${BUILD_DATE}

# Install iproute2 (tc command for ground link mirred redirect)
# Install iptables (cni0 egress lockdown for session pod security)
# Install iputils-ping (substrate latency measurement to VXLAN peers)
# Install kmod so the privileged Node Agent can load MPLS kernel modules
# Install util-linux for nsenter used by namespace wiring helpers
RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 iptables iputils-ping kmod util-linux \
    && rm -rf /var/lib/apt/lists/*

# Install crictl for container PID discovery
ARG CRICTL_VERSION=v1.35.0
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && curl -fsSL "https://github.com/kubernetes-sigs/cri-tools/releases/download/${CRICTL_VERSION}/crictl-${CRICTL_VERSION}-linux-amd64.tar.gz" \
       | tar -xz -C /usr/local/bin \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
RUN pip install --no-cache-dir \
    pydantic>=2.0 pyroute2 nats-py protobuf>=6.31.1 \
    pyyaml kubernetes

# Add lib/ and services/ to Python path
ENV PYTHONPATH=/app/lib:/app/services:/app

# Copy shared library (includes proto/)
COPY lib/ lib/

# Copy node_agent service
COPY services/node_agent/ services/node_agent/

ENTRYPOINT ["python", "-m", "node_agent"]
