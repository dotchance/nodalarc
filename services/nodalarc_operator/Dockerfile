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
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.version="${PROJECT_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}"
ENV NODALARC_VERSION=${PROJECT_VERSION} \
    NODALARC_BUILD_REVISION=${VCS_REF} \
    NODALARC_BUILD_DATE=${BUILD_DATE}
WORKDIR /app

# openssh-client for ssh-keygen (terminal access SSH keypair generation)
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY build/runtime-constraints.txt /tmp/nodalarc-runtime-constraints.txt

RUN pip install --no-cache-dir \
    --constraint /tmp/nodalarc-runtime-constraints.txt \
    kopf kubernetes "pydantic>=2.0" pyyaml jinja2 numpy sgp4 skyfield nats-py anise

# Add lib/ and services/ to Python path
ENV PYTHONPATH=/app/lib:/app/services:/app

# Copy shared library (includes canonical configuration models and orbital helpers)
COPY lib/ lib/

# Copy operator service
COPY services/nodalarc_operator/ services/nodalarc_operator/

# Copy workload adapters (technology-specific config translation the operator
# calls through the explicit registry). /app is already on PYTHONPATH.
COPY adapters/ adapters/

# Copy platform configuration and static catalog data used by the resolver.
COPY configs/ configs/
# Convert the ephemeris frame kernels for ANISE once, at build time, so the
# running service loads only local, pinned, already-converted data. -B keeps
# the run from leaving Python bytecode caches in /app.
RUN python -B -m nodalarc.physics /app/configs/ephemerides
COPY catalog/ catalog/

ENTRYPOINT ["kopf", "run", "-m", "nodalarc_operator"]
CMD ["--namespace", "nodalarc", "--liveness=http://0.0.0.0:8090/healthz"]

# NodalArc's license, its additional terms and the third-party notices go with
# every image whose label names AGPL-3.0-only (AGPL-3.0 Sections 4 and 6). The
# last instruction, so a change to them rebuilds no other layer.
COPY --from=nodalarc-legal LICENSE NOTICE THIRD_PARTY_NOTICES.md /usr/share/doc/nodalarc/
