FROM quay.io/frrouting/frr:10.3.1

# Dropbear: lightweight SSH daemon (~110KB) for interactive terminal access.
# Users SSH in and land directly in vtysh (login shell).
# Key-only auth, no password login, root login disabled.
# libcap for setcap (used below to grant traceroute raw socket capability)
RUN apk add --no-cache dropbear traceroute libcap && \
    setcap cap_net_raw+ep /usr/bin/traceroute && \
    adduser -D -s /usr/bin/vtysh -G frrvty operator && \
    mkdir -p /home/operator/.ssh && \
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
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 22

ENTRYPOINT ["/sbin/tini", "--"]
CMD ["/entrypoint.sh"]
