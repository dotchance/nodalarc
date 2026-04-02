# Node Agent container — executes privileged netlink operations on each
# K3s node, receives commands from Scheduler via NATS request/reply.
#
# Requires hostPID: true and privileged: true in the DaemonSet spec.
# Needs iproute2 for tc commands and crictl for container PID discovery.
#
# Build context must be the repo root:
#   docker build -f services/node_agent/Dockerfile -t nodalarc/node-agent:latest .

FROM python:3.14-slim

# Install iproute2 (tc command for ground link mirred redirect)
RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

# Install crictl for container PID discovery
ARG CRICTL_VERSION=v1.32.0
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
