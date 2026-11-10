# FRR is pinned to 10.6.2. FRR 10.7.0 added a zebra shortcut that skips the kernel write when a
# route is re-sent with an unchanged next-hop group id. When a router's only link loses carrier,
# the kernel deletes that group and the routes that use it without telling zebra. Zebra re-creates
# the group under the same id and never writes those routes back, while it reports them installed.
# On FRR 10.7.1 a lunar station lost its route to its relay this way after one link drop and
# return; on 10.6.2 the same sequence restored every route. The upstream fix is pending:
# https://github.com/FRRouting/frr/pull/23560
FROM quay.io/frrouting/frr:10.6.2@sha256:89e5e1ec8b17c5dffaf57dbac58a29ebeb3731a84e5bc09dad1662adff2ba7f5
ARG PROJECT_VERSION=0+unknown
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.title="NodalArc FRR" \
      org.opencontainers.image.description="FRR router image for NodalArc emulation nodes" \
      org.opencontainers.image.vendor=".chance (dotchance)" \
      org.opencontainers.image.authors=".chance (dotchance)" \
      org.opencontainers.image.source="https://github.com/dotchance/nodalarc" \
      org.opencontainers.image.url="https://nodal.asmolab.net" \
      org.opencontainers.image.documentation="https://github.com/dotchance/nodalarc" \
      org.opencontainers.image.licenses="AGPL-3.0-only AND GPL-2.0-or-later" \
      org.opencontainers.image.version="${PROJECT_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}"
ENV NODALARC_VERSION=${PROJECT_VERSION} \
    NODALARC_BUILD_REVISION=${VCS_REF} \
    NODALARC_BUILD_DATE=${BUILD_DATE}

# OpenSSH server for interactive terminal access (vtysh login shell).
# Users SSH in and land directly in vtysh — same as a real router.
# Key-only auth, no password login, root login disabled, UseDNS no.
#
# The traceroute package replaces BusyBox's applet at /usr/bin/traceroute.
# It needs no raw socket, so the operator user can run it from vtysh in a pod
# that forbids privilege escalation. traceroute6 is the same binary under its
# IPv6 name.
RUN apk add --no-cache openssh-server traceroute && \
    ln -sf traceroute /usr/bin/traceroute6 && \
    adduser -D -s /usr/bin/vtysh -G frrvty operator && \
    passwd -u operator && \
    mkdir -p /home/operator/.ssh /etc/ssh && \
    chown -R operator:frrvty /home/operator/.ssh && \
    chmod 700 /home/operator/.ssh && \
    echo "/usr/bin/vtysh" >> /etc/shells && \
    printf '\n  NodalArc Constellation Emulation Node\n  https://github.com/dotchance/nodalarc\n\n' > /etc/motd

# Security note: bash and ash are NOT removed from this image.
# FRR's docker-start and frrcommon.sh are bash scripts — removing bash
# breaks FRR's startup chain. The security boundary for interactive
# terminal access is: SSH login shell = /usr/bin/vtysh (not bash),
# vtysh "terminal shell" disabled (verified in official FRR 10.6.2),
# read-only root filesystem (set by session_deployer.py).

# The official FRR image carries no license text. FRR's COPYING and the
# license texts it points to (doc/licenses) come from the commit of the
# frr-10.6.2 tag, each checked against its SHA-256, and land in
# /usr/share/doc/frr.
ARG FRR_SOURCE=https://raw.githubusercontent.com/FRRouting/frr/c8efb33af96f9f1220d0fdda6ac049f9126edd6f
ADD --checksum=sha256:7bf053957d6c38e39a06a112c60ff35b228d3bd03edbe8c9a03508b051128d16 ${FRR_SOURCE}/COPYING /usr/share/doc/frr/COPYING
ADD --checksum=sha256:f9e0c21faf738da586281c0beaad3cd56204c19085a10fe05e4d3d7f8b5863d2 ${FRR_SOURCE}/doc/licenses/BSD-2-Clause /usr/share/doc/frr/licenses/BSD-2-Clause
ADD --checksum=sha256:aca297b8fd3a43e3bb200f1e2e043c0db6c897325f5ff7f23f7530302a6bf713 ${FRR_SOURCE}/doc/licenses/BSD-3-Clause /usr/share/doc/frr/licenses/BSD-3-Clause
ADD --checksum=sha256:8177f97513213526df2cf6184d8ff986c675afb514d4e68a404010521b880643 ${FRR_SOURCE}/doc/licenses/GPL-2.0 /usr/share/doc/frr/licenses/GPL-2.0
ADD --checksum=sha256:8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903 ${FRR_SOURCE}/doc/licenses/GPL-3.0 /usr/share/doc/frr/licenses/GPL-3.0
ADD --checksum=sha256:6d80da3224cbdcf016ea554e00fc9cd933bd1a3417cb5e5b7abf59f6d126761d ${FRR_SOURCE}/doc/licenses/ISC /usr/share/doc/frr/licenses/ISC
ADD --checksum=sha256:dc626520dcd53a22f727af3ee42c770e56c97a64fe3adb063799d8ab032fe551 ${FRR_SOURCE}/doc/licenses/LGPL-2.1 /usr/share/doc/frr/licenses/LGPL-2.1
ADD --checksum=sha256:2a1124bf8bc60f1f59b67e15e3d261b5d60fcd9674a3eace0a5502fd4287d528 ${FRR_SOURCE}/doc/licenses/LicenseRef-Skiplist-BSD-0-Clause /usr/share/doc/frr/licenses/LicenseRef-Skiplist-BSD-0-Clause
ADD --checksum=sha256:074415b57c6ba1780483d89e55f40dec3ded96c28aaf47d63c486683ba553e7c ${FRR_SOURCE}/doc/licenses/MIT /usr/share/doc/frr/licenses/MIT
ADD --checksum=sha256:b5065838cbac452dfc855ba6e6e031481ad2c68406f70d21ead9321374653e6c ${FRR_SOURCE}/doc/licenses/Unlicense /usr/share/doc/frr/licenses/Unlicense
RUN chmod -R a+rX /usr/share/doc/frr

# Our entrypoint configures SSH, then hands off to FRR's stock
# docker-start which uses watchfrr to manage daemons.
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 22

ENTRYPOINT ["/sbin/tini", "--"]
CMD ["/entrypoint.sh"]

# NodalArc's license, its additional terms, the third-party notices and the
# license texts they name (LICENSES/) go with every image whose label names
# AGPL-3.0-only (AGPL-3.0 Sections 4 and 6). The last instructions, so a change
# to them rebuilds no other layer.
COPY --from=nodalarc-legal LICENSE NOTICE THIRD_PARTY_NOTICES.md /usr/share/doc/nodalarc/
COPY --from=nodalarc-legal LICENSES/ /usr/share/doc/nodalarc/LICENSES/
