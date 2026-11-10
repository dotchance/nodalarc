# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
# Scheduler container — consumes OME NATS events, dispatches topology
# changes to Node Agents via NATS request/reply.
#
# All session config comes from ConfigMap mounts at /etc/nodalarc/.
# Static catalog reference data is baked into the image.
#
# Build context must be the repo root:
#   docker build --build-context nodalarc-legal=. -f services/scheduler/Dockerfile -t nodalarc/scheduler:latest .

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
    "pydantic>=2.0" nats-py pyyaml numpy "protobuf>=6.31.1" \
    kubernetes jinja2 sgp4 skyfield anise

# Add lib/ and services/ to Python path
ENV PYTHONPATH=/app/lib:/app/services:/app

# Copy shared library (includes proto/, canonical configuration models, and geometry helpers)
COPY lib/ lib/
# Workload adapter support declarations: session resolution reads them
# through the adapter registry. /app is already on PYTHONPATH.
COPY adapters/ adapters/

# Copy scheduler service
COPY services/scheduler/ services/scheduler/

# Copy static catalog reference data
COPY catalog/ catalog/
COPY configs/ephemerides/ configs/ephemerides/
# Convert the ephemeris frame kernels for ANISE once, at build time, so the
# running service loads only local, pinned, already-converted data. -B keeps
# the run from leaving Python bytecode caches in /app.
RUN python -B -m nodalarc.physics /app/configs/ephemerides

ENTRYPOINT ["python", "-m", "scheduler"]

# NodalArc's license, its additional terms, the third-party notices and the
# license texts they name (LICENSES/) go with every image whose label names
# AGPL-3.0-only (AGPL-3.0 Sections 4 and 6). The last instructions, so a change
# to them rebuilds no other layer.
COPY --from=nodalarc-legal LICENSE NOTICE THIRD_PARTY_NOTICES.md /usr/share/doc/nodalarc/
COPY --from=nodalarc-legal LICENSES/ /usr/share/doc/nodalarc/LICENSES/
