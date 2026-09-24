#!/bin/bash
# Copyright 2024-2026 .chance (dotchance)
# One-time host bootstrap for NodalArc.
#
# Installs K3s, Docker, uv, Node.js, Helm, sets the host network MTU, and
# configures the kernel for MPLS forwarding and network namespace operations.
#
# Idempotent — safe to run multiple times.
# Requires root (or sudo).
#
# After this script completes: cd nodal && make all

set -euo pipefail

echo "=== NodalArc Host Bootstrap ==="
echo "Copyright 2024-2026 .chance (dotchance)"
echo "Official source: https://github.com/dotchance/nodalarc"

# ---------------------------------------------------------------------------
# Detect OS
# ---------------------------------------------------------------------------

if [ ! -f /etc/os-release ]; then
    echo "ERROR: Cannot detect OS. This script supports Ubuntu/Debian."
    exit 1
fi
. /etc/os-release

if [[ "$ID" != "ubuntu" && "$ID" != "debian" ]]; then
    echo "WARNING: Untested OS ($ID). Continuing anyway..."
fi

# ---------------------------------------------------------------------------
# System packages
# ---------------------------------------------------------------------------

echo "[1/9] Installing system packages..."
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
    curl ca-certificates gnupg lsb-release jq git \
    iproute2 iptables

# ---------------------------------------------------------------------------
# Host network MTU
# ---------------------------------------------------------------------------
# Emulated interfaces carry packets up to 9000 bytes whether their pods share a
# host or not. Between hosts those packets travel inside VXLAN, which adds 50
# bytes over IPv4 and 70 over IPv6, so the interface that carries cluster
# traffic runs a 9100-byte MTU and the switch between hosts must carry jumbo
# frames. It is set before K3s starts so the pod network derives its MTU from
# it. The Node Agent proves the path between hosts before it wires a session.

HOST_MTU=9100
echo "[2/9] Setting the cluster interface MTU to ${HOST_MTU}..."
# K3s takes its node address from the interface of the default route.
CLUSTER_INTERFACE="$(ip -o route get 1.1.1.1 | sed -n 's/.* dev \([^ ]*\) .*/\1/p')"
if [ -z "$CLUSTER_INTERFACE" ]; then
    echo "ERROR: no default route; cannot identify the interface that carries cluster traffic."
    exit 1
fi
MAX_MTU="$(ip -d link show dev "$CLUSTER_INTERFACE" | sed -n 's/.* maxmtu \([0-9]*\).*/\1/p')"
if [ -z "$MAX_MTU" ] || [ "$MAX_MTU" -lt "$HOST_MTU" ]; then
    echo "ERROR: $CLUSTER_INTERFACE supports an MTU of at most ${MAX_MTU:-unknown}; NodalArc requires ${HOST_MTU}."
    exit 1
fi
if ! command -v netplan >/dev/null 2>&1 || [ ! -d /etc/netplan ]; then
    echo "ERROR: this host does not use netplan. Set a persistent ${HOST_MTU}-byte MTU on"
    echo "       $CLUSTER_INTERFACE with the host's network configuration, then rerun."
    exit 1
fi
cat > /etc/netplan/60-nodalarc-mtu.yaml <<NETPLAN
network:
  version: 2
  ethernets:
    ${CLUSTER_INTERFACE}:
      mtu: ${HOST_MTU}
NETPLAN
chmod 600 /etc/netplan/60-nodalarc-mtu.yaml
netplan generate
if [ "$(netplan get "ethernets.${CLUSTER_INTERFACE}.mtu")" != "$HOST_MTU" ]; then
    echo "ERROR: netplan does not carry an MTU of ${HOST_MTU} for $CLUSTER_INTERFACE after the drop-in."
    exit 1
fi
# The network configuration applies the MTU itself, so it holds when the link
# renegotiates. Some NICs reset their link to change MTU; the interface is
# checked once the reset settles.
netplan apply
for _ in $(seq 1 30); do
    [ "$(cat "/sys/class/net/${CLUSTER_INTERFACE}/mtu")" = "$HOST_MTU" ] \
        && [ "$(cat "/sys/class/net/${CLUSTER_INTERFACE}/operstate")" = "up" ] && break
    sleep 1
done
if [ "$(cat "/sys/class/net/${CLUSTER_INTERFACE}/mtu")" != "$HOST_MTU" ] \
    || [ "$(cat "/sys/class/net/${CLUSTER_INTERFACE}/operstate")" != "up" ]; then
    echo "ERROR: $CLUSTER_INTERFACE is not up at an MTU of ${HOST_MTU} after applying the configuration."
    exit 1
fi
echo "  $CLUSTER_INTERFACE: MTU ${HOST_MTU}, persistent in /etc/netplan/60-nodalarc-mtu.yaml"

# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------

if command -v docker &>/dev/null; then
    echo "[3/9] Docker already installed: $(docker --version)"
else
    echo "[3/9] Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    usermod -aG docker "${SUDO_USER:-$USER}" 2>/dev/null || true
    echo "  NOTE: Log out and back in for docker group to take effect."
fi

# ---------------------------------------------------------------------------
# K3s
# ---------------------------------------------------------------------------

if command -v k3s &>/dev/null; then
    echo "[4/9] K3s already installed: $(k3s --version | head -1)"
else
    echo "[4/9] Installing K3s..."
    curl -sfL https://get.k3s.io | sh -s - \
        --write-kubeconfig-mode 644 \
        --disable traefik
fi

# Make kubeconfig accessible without sudo
KUBECONFIG_SRC="/etc/rancher/k3s/k3s.yaml"
KUBECONFIG_DST="${HOME}/.kube/config"
if [ -f "$KUBECONFIG_SRC" ]; then
    mkdir -p "$(dirname "$KUBECONFIG_DST")"
    cp "$KUBECONFIG_SRC" "$KUBECONFIG_DST"
    if [ -n "${SUDO_USER:-}" ]; then
        chown "${SUDO_USER}:${SUDO_USER}" "$KUBECONFIG_DST"
    fi
    chmod 600 "$KUBECONFIG_DST"
    echo "  Kubeconfig copied to $KUBECONFIG_DST"
fi

# ---------------------------------------------------------------------------
# kubectl + Helm
# ---------------------------------------------------------------------------

if command -v kubectl &>/dev/null; then
    echo "[5/9] kubectl already installed: $(kubectl version --client --short 2>/dev/null || kubectl version --client)"
else
    echo "[5/9] Installing kubectl..."
    curl -fsSL "https://dl.k8s.io/release/$(curl -fsSL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" \
        -o /usr/local/bin/kubectl
    chmod +x /usr/local/bin/kubectl
fi

if command -v helm &>/dev/null; then
    echo "[6/9] Helm already installed: $(helm version --short)"
else
    echo "[6/9] Installing Helm..."
    curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
fi

# ---------------------------------------------------------------------------
# Node.js
# ---------------------------------------------------------------------------

if command -v node &>/dev/null && [ "$(node --version | cut -d. -f1 | tr -d v)" -ge 22 ]; then
    echo "[7/9] Node.js already installed: $(node --version)"
else
    echo "[7/9] Installing Node.js 22..."
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
    apt-get install -y -qq nodejs
fi

# ---------------------------------------------------------------------------
# uv (Python package manager)
# ---------------------------------------------------------------------------

if command -v uv &>/dev/null; then
    echo "[8/9] uv already installed: $(uv --version)"
else
    echo "[8/9] Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Make available to current session
    export PATH="$HOME/.local/bin:$PATH"
fi

# ---------------------------------------------------------------------------
# Kernel modules and sysctls
# ---------------------------------------------------------------------------

echo "[9/9] Configuring kernel for MPLS and network namespaces..."

# Load MPLS kernel modules
modprobe mpls_router 2>/dev/null || true
modprobe mpls_iptunnel 2>/dev/null || true

# Persist modules across reboots
for mod in mpls_router mpls_iptunnel; do
    grep -qxF "$mod" /etc/modules-load.d/nodalarc.conf 2>/dev/null || \
        echo "$mod" >> /etc/modules-load.d/nodalarc.conf
done

# Sysctls for MPLS and IP forwarding
cat > /etc/sysctl.d/99-nodalarc.conf <<'SYSCTL'
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
net.mpls.platform_labels = 1048575
net.mpls.conf.lo.input = 1
SYSCTL
sysctl --system -q

echo ""
echo "=== Bootstrap complete ==="
echo ""
echo "Next steps:"
echo "  cd nodal"
echo "  make all"
echo ""
