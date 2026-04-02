#!/bin/bash
# One-time host bootstrap for NodalArc.
#
# Installs K3s, Docker, uv, Node.js, Helm, and configures the kernel
# for MPLS forwarding and network namespace operations.
#
# Idempotent — safe to run multiple times.
# Requires root (or sudo).
#
# After this script completes: cd nodal && make all

set -euo pipefail

echo "=== NodalArc Host Bootstrap ==="

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

echo "[1/8] Installing system packages..."
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
    curl ca-certificates gnupg lsb-release jq git \
    iproute2 iptables

# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------

if command -v docker &>/dev/null; then
    echo "[2/8] Docker already installed: $(docker --version)"
else
    echo "[2/8] Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    usermod -aG docker "${SUDO_USER:-$USER}" 2>/dev/null || true
    echo "  NOTE: Log out and back in for docker group to take effect."
fi

# ---------------------------------------------------------------------------
# K3s
# ---------------------------------------------------------------------------

if command -v k3s &>/dev/null; then
    echo "[3/8] K3s already installed: $(k3s --version | head -1)"
else
    echo "[3/8] Installing K3s..."
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
    echo "[4/8] kubectl already installed: $(kubectl version --client --short 2>/dev/null || kubectl version --client)"
else
    echo "[4/8] Installing kubectl..."
    curl -fsSL "https://dl.k8s.io/release/$(curl -fsSL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" \
        -o /usr/local/bin/kubectl
    chmod +x /usr/local/bin/kubectl
fi

if command -v helm &>/dev/null; then
    echo "[5/8] Helm already installed: $(helm version --short)"
else
    echo "[5/8] Installing Helm..."
    curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
fi

# ---------------------------------------------------------------------------
# Node.js
# ---------------------------------------------------------------------------

if command -v node &>/dev/null && [ "$(node --version | cut -d. -f1 | tr -d v)" -ge 22 ]; then
    echo "[6/8] Node.js already installed: $(node --version)"
else
    echo "[6/8] Installing Node.js 22..."
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
    apt-get install -y -qq nodejs
fi

# ---------------------------------------------------------------------------
# uv (Python package manager)
# ---------------------------------------------------------------------------

if command -v uv &>/dev/null; then
    echo "[7/8] uv already installed: $(uv --version)"
else
    echo "[7/8] Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Make available to current session
    export PATH="$HOME/.local/bin:$PATH"
fi

# ---------------------------------------------------------------------------
# Kernel modules and sysctls
# ---------------------------------------------------------------------------

echo "[8/8] Configuring kernel for MPLS and network namespaces..."

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
