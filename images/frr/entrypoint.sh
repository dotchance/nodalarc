#!/bin/bash
set -e

# Wait for the Operator to deliver FRR configs.
# The Operator execs into the pod to:
#   1. cp /etc/frr-config/* /etc/frr/
#   2. touch /etc/frr/.config-ready
READY_FILE="/etc/frr/.config-ready"
TIMEOUT=900
WAITED=0

echo "Waiting for config (sentinel: $READY_FILE)..."
while [ ! -f "$READY_FILE" ]; do
    sleep 1
    WAITED=$((WAITED + 1))
    if [ "$WAITED" -ge "$TIMEOUT" ]; then
        echo "ERROR: Config not delivered within ${TIMEOUT}s, exiting"
        exit 1
    fi
done
echo "Config ready after ${WAITED}s"

# Ensure FRR owns config directory
chown -R frr:frr /etc/frr

# Hand off to FRR's stock docker-start (watchfrr reads /etc/frr/daemons)
exec /usr/lib/frr/docker-start
