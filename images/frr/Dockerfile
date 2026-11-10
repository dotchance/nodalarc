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

# Our entrypoint configures SSH, then hands off to FRR's stock
# docker-start which uses watchfrr to manage daemons.
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 22

ENTRYPOINT ["/sbin/tini", "--"]
CMD ["/entrypoint.sh"]
