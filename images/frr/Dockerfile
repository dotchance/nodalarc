FROM quay.io/frrouting/frr:10.3.1

# Security hardening: remove bash and ash shells.
# /bin/sh (busybox) is kept — required by entrypoint.sh and FRR's docker-start.
# vtysh's "terminal shell" command is already disabled in the official FRR image.
# The SSH login shell (Phase 1) will be /usr/bin/vtysh, not /bin/sh.
RUN rm -f /bin/bash /bin/ash

# Our entrypoint waits for the Operator to deliver configs via
# kubectl exec (cp + sentinel touch), then hands off to FRR's
# stock docker-start which uses watchfrr to manage daemons.
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/sbin/tini", "--"]
CMD ["/entrypoint.sh"]
