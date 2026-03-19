# Node Agent container — executes privileged netlink operations on each
# K3s node via gRPC from the Scheduler.
#
# Requires hostPID: true and privileged: true in the DaemonSet spec
# (not in this Dockerfile). Needs iproute2 for tc commands and crictl
# for container PID discovery.
#
# Build context must be the repo root:
#   docker build -f node_agent/Dockerfile -t nodalarc/node-agent:latest .

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
# No grpcio — ZMQ transport instead (gRPC C extension incompatible
# with hostPID containers). Proto messages used for serialization only.
RUN pip install --no-cache-dir \
    pydantic>=2.0 pyroute2 pyzmq protobuf>=6.31.1 \
    pyyaml kubernetes

# Add lib/ to Python path so nodalarc package is importable
ENV PYTHONPATH=/app/lib:/app

# Copy shared library and node_agent module
COPY lib/ lib/
COPY node_agent/ node_agent/

# gRPC listen port
EXPOSE 50100

ENTRYPOINT ["python", "-m", "node_agent"]
