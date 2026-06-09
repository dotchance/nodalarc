# Scheduler container — consumes OME NATS events, dispatches topology
# changes to Node Agents via NATS request/reply.
#
# All session config comes from ConfigMap mounts at /etc/nodalarc/.
# Static catalog reference data is baked into the image.
#
# Build context must be the repo root:
#   docker build -f services/scheduler/Dockerfile -t nodalarc/scheduler:latest .

FROM python:3.14-slim@sha256:5b3879b6f3cb77e712644d50262d05a7c146b7312d784a18eff7ff5462e77033
ARG PROJECT_VERSION=0+unknown
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.title="NodalArc Scheduler" \
      org.opencontainers.image.description="Topology scheduler and dispatcher for NodalArc" \
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
    "pydantic>=2.0" nats-py pyyaml numpy "grpcio>=1.74" "protobuf>=6.31.1" \
    kubernetes jinja2 sgp4 skyfield "httpx>=0.28.1"

# Add lib/ and services/ to Python path
ENV PYTHONPATH=/app/lib:/app/services:/app

# Copy shared library (includes proto/, constellation_loader, geo, models)
COPY lib/ lib/

# Copy scheduler service
COPY services/scheduler/ services/scheduler/

# Copy static catalog reference data
COPY catalog/ catalog/
COPY configs/ephemerides/ configs/ephemerides/

ENTRYPOINT ["python", "-m", "scheduler"]
