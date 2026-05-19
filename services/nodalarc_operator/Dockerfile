# K8s Operator container — watches ConstellationSpec CRD, manages sessions.
#
# Build context must be the repo root:
#   docker build -f services/nodalarc_operator/Dockerfile -t nodalarc/operator:latest .

FROM python:3.14-slim@sha256:5b3879b6f3cb77e712644d50262d05a7c146b7312d784a18eff7ff5462e77033
ARG PROJECT_VERSION=0+unknown
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.title="NodalArc Operator" \
      org.opencontainers.image.description="Kubernetes operator for NodalArc session deployment" \
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
WORKDIR /app

# iputils-ping for substrate latency measurement between nodes (M10)
# openssh-client for ssh-keygen (terminal access SSH keypair generation)
RUN apt-get update && apt-get install -y --no-install-recommends \
    iputils-ping iputils-tracepath openssh-client \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    kopf kubernetes pydantic>=2.0 pyyaml jinja2 numpy sgp4 skyfield nats-py

# Add lib/ and services/ to Python path
ENV PYTHONPATH=/app/lib:/app/services:/app

# Copy shared library (includes constellation_loader, orbital, models)
COPY lib/ lib/

# Copy operator service
COPY services/nodalarc_operator/ services/nodalarc_operator/

# Copy configs (FRR templates, satellite types, ground stations, etc.)
COPY configs/ configs/

CMD ["kopf", "run", "-m", "nodalarc_operator", "--namespace", "nodalarc", "--liveness=http://0.0.0.0:8090/healthz"]
