#!/bin/bash
set -e

# Wait for FRR config to be available.
# Two delivery mechanisms (backwards compatible):
#   1. ConfigMap volume mount (M7 Operator): staged at /etc/frr-config/
#   2. Legacy kubectl cp + sentinel: na-deploy touches .config-ready after copy
READY_FILE="/etc/frr/.config-ready"
STAGING_DIR="/etc/frr-config"
TIMEOUT=900
WAITED=0

echo "Waiting for config (staging: $STAGING_DIR/daemons or sentinel: $READY_FILE)..."
while [ ! -f "$STAGING_DIR/daemons" ] && [ ! -f "$READY_FILE" ]; do
    sleep 1
    WAITED=$((WAITED + 1))
    if [ "$WAITED" -ge "$TIMEOUT" ]; then
        echo "ERROR: Config not delivered within ${TIMEOUT}s, exiting"
        exit 1
    fi
done
echo "Config ready after ${WAITED}s"

# If configs are in staging dir (ConfigMap mount), copy to /etc/frr/
if [ -d "$STAGING_DIR" ] && [ -f "$STAGING_DIR/daemons" ]; then
    echo "Copying configs from staging mount to /etc/frr/"
    cp "$STAGING_DIR"/*.conf /etc/frr/ 2>/dev/null || true
    cp "$STAGING_DIR/daemons" /etc/frr/daemons
fi

# Create runit run scripts for enabled FRR daemons
DAEMONS_FILE="/etc/frr/daemons"

# mgmtd must start before all other daemons in FRR 10.x — it is the
# configuration management daemon that replaces the old config model.
# staticd's "ip route ... label" commands require mgmtd to be running.
for daemon in mgmtd zebra isisd ospfd pathd staticd; do
    enabled=$(grep "^${daemon}=yes" "$DAEMONS_FILE" || true)
    if [ -n "$enabled" ]; then
        mkdir -p "/etc/service/${daemon}"
        # mgmtd doesn't take -f config file; zebra/staticd use -A for listen address
        if [ "$daemon" = "mgmtd" ]; then
            cat > "/etc/service/${daemon}/run" <<RUNEOF
#!/bin/bash
exec /usr/lib/frr/${daemon}
RUNEOF
        else
            cat > "/etc/service/${daemon}/run" <<RUNEOF
#!/bin/bash
exec /usr/lib/frr/${daemon} -f /etc/frr/${daemon}.conf -A 127.0.0.1
RUNEOF
        fi
        chmod +x "/etc/service/${daemon}/run"
    else
        rm -rf "/etc/service/${daemon}"
    fi
done

# Ensure FRR owns config directory
chown -R frr:frr /etc/frr

# Start runit as PID 1 supervisor
exec runsvdir /etc/service
