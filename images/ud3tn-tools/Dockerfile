# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
# NodalArc uD3TN tools image: a generic AAP 2.0 client for uD3TN nodes.
#
# This image packages D3TN's ud3tn-utils Python tools (BSD-3-Clause OR
# Apache-2.0, installed from PyPI; pip keeps the license text in the
# package's dist-info directory) so a workload profile can configure
# contacts, send bundles, receive bundles, and drive the storage agent of
# a co-located uD3TN daemon. It carries no NodalArc code and no tuning:
# node identity, contact schedules, and peer addresses arrive as container
# commands and environment from the consuming workload profile.
#
# The container runs as root deliberately: the upstream uD3TN daemon runs
# as root and creates its AAP 2.0 admin socket with mode 0755, so the
# administrative client must share that identity to connect. The container
# keeps a read-only root filesystem and no added capabilities in the
# consuming platform.

# The Python base is pinned by digest so a rebuild starts from the same image.
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
ARG PROJECT_VERSION=0+unknown
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.title="NodalArc uD3TN tools" \
      org.opencontainers.image.description="D3TN ud3tn-utils 0.15.0 AAP 2.0 tools for NodalArc uD3TN nodes" \
      org.opencontainers.image.vendor=".chance (dotchance)" \
      org.opencontainers.image.authors=".chance (dotchance)" \
      org.opencontainers.image.source="https://github.com/dotchance/nodalarc" \
      org.opencontainers.image.url="https://nodal.asmolab.net" \
      org.opencontainers.image.documentation="https://github.com/dotchance/nodalarc" \
      org.opencontainers.image.licenses="BSD-3-Clause OR Apache-2.0" \
      org.opencontainers.image.version="${PROJECT_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}"
ENV NODALARC_VERSION=${PROJECT_VERSION} \
    NODALARC_BUILD_REVISION=${VCS_REF} \
    NODALARC_BUILD_DATE=${BUILD_DATE}

# iproute2/iputils: endpoint diagnostics (ip, ping, tracepath) so a DTN
# node is inspectable from inside its own container. Debian stamps a
# cap_net_raw file capability on ping at install; a NoNewPrivs container
# refuses to exec any file-capability binary, so the capability and the
# setuid fallback are both stripped. ping instead uses unprivileged ICMP
# datagram sockets, which the consuming platform enables via the
# net.ipv4.ping_group_range sysctl.
RUN apt-get update && apt-get install -y --no-install-recommends \
        iproute2 iputils-ping iputils-tracepath libcap2-bin \
    && rm -rf /var/lib/apt/lists/* \
    && if getcap /usr/bin/ping | grep -q .; then setcap -r /usr/bin/ping; fi \
    && chmod u-s /usr/bin/ping \
    && test -z "$(getcap /usr/bin/ping)"

RUN pip install --no-cache-dir ud3tn-utils==0.15.0
