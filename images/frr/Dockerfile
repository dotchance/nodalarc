FROM quay.io/frrouting/frr:10.3.1

# Security note: bash and ash are NOT removed from this image.
# FRR's docker-start and frrcommon.sh are bash scripts — removing bash
# breaks FRR's startup chain. The security boundary for interactive
# terminal access is: SSH login shell = /usr/bin/vtysh (not bash),
# vtysh "terminal shell" disabled (verified in official FRR image),
# read-only root filesystem (set by session_deployer.py).
# The user has no path from vtysh to bash.

# Our entrypoint waits for the Operator to deliver configs via
# kubectl exec (cp + sentinel touch), then hands off to FRR's
# stock docker-start which uses watchfrr to manage daemons.
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/sbin/tini", "--"]
CMD ["/entrypoint.sh"]
