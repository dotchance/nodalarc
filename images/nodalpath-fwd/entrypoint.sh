#!/bin/bash
set -e

# ---------------------------------------------------------------------------
# nodalpath-fwd container entrypoint
#
# This script prepares the kernel MPLS dataplane inside the container before
# starting the gRPC forwarding service (fwd_server.py). It runs with
# CAP_SYS_ADMIN so it can modify sysctl parameters.
# ---------------------------------------------------------------------------

# Enable MPLS kernel modules (requires CAP_SYS_ADMIN).
#
# platform_labels sets the maximum MPLS label value the kernel will accept
# in its routing table. We set it to 1048575 (2^20 - 1), the maximum
# allowed by the MPLS spec. Without this, `ip -f mpls route add` commands
# in fwd_server.py will fail with EINVAL for any label value.
#
# KNOWN ISSUE: In K3s (and some other container runtimes), /proc/sys is
# mounted read-only inside containers even with CAP_SYS_ADMIN. When this
# happens, the sysctl write fails and we fall through to the warning.
# The host-side orchestrator (link_manager.enable_mpls_input) sets
# platform_labels from outside the container via nsenter as a fallback.
# If NEITHER succeeds, MPLS forwarding will silently fail — routes will
# be accepted by iproute2 but the kernel will not actually forward
# labeled packets.
echo "Enabling MPLS kernel support..."
sysctl -w net.mpls.platform_labels=1048575 2>/dev/null || echo "WARN: Could not set platform_labels (sysctl may be read-only, host orchestrator should set this via nsenter)"

# Enable MPLS input on all interfaces currently present.
# New interfaces created later (veths moved in by the orchestrator) will
# have MPLS input enabled by link_manager.enable_mpls_input via nsenter.
for iface in $(ls /sys/class/net/); do
    sysctl -w "net.mpls.conf.${iface}.input=1" 2>/dev/null || true
done

# Enable IPv4 forwarding (required for IP-level re-routing after MPLS POP)
sysctl -w net.ipv4.ip_forward=1 2>/dev/null || true

# Assign loopback address if provided.
#
# LOOPBACK_IPV4 is the node's routable loopback address (e.g., 10.0.P.S/32).
# This address serves two critical purposes in the MPLS forwarding model:
#
#   1. MPLS packet termination: When fwd_server installs a POP rule
#      (`ip -f mpls route replace <SID> via inet 127.0.0.1 dev lo`),
#      the kernel pops the MPLS header and delivers the inner IP packet
#      to the loopback. The loopback must have a routable address so the
#      IP FIB can match it as a local destination or re-route it. Without
#      LOOPBACK_IPV4, the decapsulated IP packet would be delivered to
#      127.0.0.1 and dropped (no matching route for the actual destination).
#
#   2. Probe source address: NodalPath traceroute and ping probes use
#      LOOPBACK_IPV4 as their source address. This ensures ICMP replies
#      follow the MPLS forwarding path back, rather than using a veth
#      link-local address that may not be reachable from remote nodes.
if [ -n "${LOOPBACK_IPV4:-}" ]; then
    echo "Assigning loopback: ${LOOPBACK_IPV4}/32"
    ip addr add "${LOOPBACK_IPV4}/32" dev lo 2>/dev/null || true
fi

# Start NETCONF stub in background (port 830)
echo "Starting NETCONF stub on port 830..."
python3 /app/netconf_stub.py &

# Start gRPC forwarding service
echo "Starting gRPC forwarding service on port 50051..."
exec python3 /app/fwd_server.py
