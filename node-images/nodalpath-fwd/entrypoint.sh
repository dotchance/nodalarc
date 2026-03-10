#!/bin/bash
set -e

# Enable MPLS kernel modules (requires CAP_SYS_ADMIN)
echo "Enabling MPLS kernel support..."
sysctl -w net.mpls.platform_labels=1048575 2>/dev/null || echo "WARN: Could not set platform_labels (sysctl may be read-only)"

# Enable MPLS on all interfaces
for iface in $(ls /sys/class/net/); do
    sysctl -w "net.mpls.conf.${iface}.input=1" 2>/dev/null || true
done

# Enable IPv4 forwarding
sysctl -w net.ipv4.ip_forward=1 2>/dev/null || true

# Start NETCONF stub in background (port 830)
echo "Starting NETCONF stub on port 830..."
python3 /app/netconf_stub.py &

# Start gRPC forwarding service
echo "Starting gRPC forwarding service on port 50051..."
exec python3 /app/fwd_server.py
