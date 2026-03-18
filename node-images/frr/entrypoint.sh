#!/bin/bash
set -e

# Wait for configs to be copied by na-deploy.
# na-deploy touches /etc/frr/.config-ready after kubectl cp completes.
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
