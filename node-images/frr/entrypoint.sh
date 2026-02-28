#!/bin/bash
set -e

# Create runit run scripts for enabled FRR daemons
DAEMONS_FILE="/etc/frr/daemons"

for daemon in zebra isisd ospfd pathd; do
    enabled=$(grep "^${daemon}=yes" "$DAEMONS_FILE" || true)
    if [ -n "$enabled" ]; then
        mkdir -p "/etc/service/${daemon}"
        cat > "/etc/service/${daemon}/run" <<RUNEOF
#!/bin/bash
exec /usr/lib/frr/${daemon} -f /etc/frr/${daemon}.conf -A 127.0.0.1
RUNEOF
        chmod +x "/etc/service/${daemon}/run"
    else
        # Remove service dir if daemon not enabled
        rm -rf "/etc/service/${daemon}"
    fi
done

# Ensure FRR owns config directory
chown -R frr:frr /etc/frr

# Start runit as PID 1 supervisor
exec runsvdir /etc/service
