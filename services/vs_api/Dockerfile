# VS-API container — platform infrastructure service.
#
# VS-API is the user's front door: the Session Builder, WebSocket streaming,
# REST API, terminal proxy, and session lifecycle orchestrator. It is a
# long-lived service that survives across session switches — the Operator
# never restarts it. Session parameters are variables, not reasons to
# restart; VS-API hot-reloads via SessionContext teardown/creation.
#
# catalog/ is the static read-only product catalog. User-authored catalog YAML
# is stored through VS-API's scoped repository and uploaded as exact files when
# a saved session is selected.
#
# Runtime data (live snapshots, link state, events) comes from NATS.
# Platform config comes from a single ConfigMap mount (platform.yaml).
# VS-API reads the live ConstellationSpec and selected ordinary-YAML ConfigMaps
# through Kubernetes APIs; it does not resolve a running session from mutable
# authoring storage after selection.
#
# Build context must be the repo root:
#   docker build -f services/vs_api/Dockerfile -t nodalarc/vs-api:latest .

FROM python:3.14-slim@sha256:5b3879b6f3cb77e712644d50262d05a7c146b7312d784a18eff7ff5462e77033
ARG PROJECT_VERSION=0+unknown
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.title="NodalArc VS-API" \
      org.opencontainers.image.description="Visualization State API and session lifecycle service for NodalArc" \
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

# Install Python dependencies
COPY build/runtime-constraints.txt /tmp/nodalarc-runtime-constraints.txt

RUN pip install --no-cache-dir \
    --constraint /tmp/nodalarc-runtime-constraints.txt \
    "pydantic>=2.0" nats-py pyyaml fastapi uvicorn \
    "websockets>=16.0" numpy \
    kubernetes sgp4 skyfield "asyncssh>=2.14.0" anise

# Add lib/ and services/ to Python path
ENV PYTHONPATH=/app/lib:/app/services:/app
ENV PYTHONUNBUFFERED=1

# Copy the shared library, canonical configuration models, and resolver.
COPY lib/ lib/
# Workload adapter support declarations: session resolution reads them
# through the adapter registry. /app is already on PYTHONPATH.
COPY adapters/ adapters/

# Copy VS-API service
COPY services/vs_api/ services/vs_api/

# Copy OME service (the Builder's world preview propagates through it)
COPY services/ome/ services/ome/

# Bake in deployment templates and the static catalog.
COPY configs/ configs/
# Convert the ephemeris frame kernels for ANISE once, at build time, so the
# running service loads only local, pinned, already-converted data. -B keeps
# the run from leaving Python bytecode caches in /app.
RUN python -B -m nodalarc.physics /app/configs/ephemerides
COPY catalog/ catalog/

# Existing published grammar linked by the Builder bootstrap.
COPY docs/ops/configuration-grammar.md docs/ops/configuration-grammar.md

# HTTP + WebSocket port
EXPOSE 8080

ENTRYPOINT ["python", "-m", "vs_api.main"]

# NodalArc's license, its additional terms and the third-party notices go with
# every image whose label names AGPL-3.0-only (AGPL-3.0 Sections 4 and 6). The
# last instruction, so a change to them rebuilds no other layer.
COPY --from=nodalarc-legal LICENSE NOTICE THIRD_PARTY_NOTICES.md /usr/share/doc/nodalarc/
