FROM quay.io/frrouting/frr:10.3.1@sha256:f90d26a9fd5c14fc5795a73b4254ac88bc3186c45bbeb220a225fb6182de812c
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.title="NodalArc FRR" \
      org.opencontainers.image.description="FRR router image for NodalArc emulation nodes" \
      org.opencontainers.image.vendor=".chance (dotchance)" \
      org.opencontainers.image.authors=".chance (dotchance)" \
      org.opencontainers.image.source="https://github.com/dotchance/nodalarc" \
      org.opencontainers.image.url="https://nodal.asmolab.net" \
      org.opencontainers.image.documentation="https://github.com/dotchance/nodalarc" \
      org.opencontainers.image.licenses="NodalArc Source Available License 1.0" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}"

# OpenSSH server for interactive terminal access (vtysh login shell).
# Users SSH in and land directly in vtysh — same as a real router.
# Key-only auth, no password login, root login disabled, UseDNS no.
RUN apk add --no-cache openssh-server libcap && \
    cp /bin/busybox /usr/local/bin/busybox-netraw && \
    setcap cap_net_raw+ep /usr/local/bin/busybox-netraw && \
    rm -f /usr/bin/traceroute && \
    ln -s /usr/local/bin/busybox-netraw /usr/bin/traceroute && \
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
# vtysh "terminal shell" disabled (verified in official FRR 10.3.1),
# read-only root filesystem (set by session_deployer.py).

# Our entrypoint configures SSH, then hands off to FRR's stock
# docker-start which uses watchfrr to manage daemons.
COPY daemons /etc/frr/daemons
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 22

ENTRYPOINT ["/sbin/tini", "--"]
CMD ["/entrypoint.sh"]
