FROM python:3.14-slim

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

# Install shared library first (PRD 13.13)
COPY lib/ /app/lib/
RUN pip install --no-cache-dir /app/lib/

# Install orchestrator dependencies
RUN pip install --no-cache-dir \
    pyroute2 \
    pyzmq \
    pyyaml \
    pydantic>=2.0 \
    kubernetes \
    jinja2

# Copy orchestrator code
COPY orchestrator/ /app/orchestrator/

CMD ["python", "-m", "orchestrator.main"]
