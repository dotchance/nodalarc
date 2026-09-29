#!/bin/bash
# Copyright 2024-2026 .chance (dotchance)
# One-time bootstrap of a NodalArc development machine: the machine a
# developer runs make on to build the images and drive the cluster. Users do
# not run it; they receive the chart and the images and never build code.
#
# Installs Docker, kubectl, Helm, Node.js and uv. It touches no kernel
# setting and no cluster: a K3s server is built by scripts/build-k3s-node.sh,
# run on that server, and the Node Agent loads what a session needs.
#
# Idempotent: safe to run more than once. Requires root (or sudo).
#
# After this script completes: cd nodal && make all

set -euo pipefail

echo "=== NodalArc Development Machine Bootstrap ==="
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

echo "[1/6] Installing system packages..."
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
    curl ca-certificates gnupg lsb-release jq git \
    iproute2 iptables

# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------

if command -v docker &>/dev/null; then
    echo "[2/6] Docker already installed: $(docker --version)"
else
    echo "[2/6] Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    usermod -aG docker "${SUDO_USER:-$USER}" 2>/dev/null || true
    echo "  NOTE: Log out and back in for docker group to take effect."
fi

# ---------------------------------------------------------------------------
# kubectl + Helm
# ---------------------------------------------------------------------------

if command -v kubectl &>/dev/null; then
    echo "[3/6] kubectl already installed: $(kubectl version --client --short 2>/dev/null || kubectl version --client)"
else
    echo "[3/6] Installing kubectl..."
    curl -fsSL "https://dl.k8s.io/release/$(curl -fsSL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" \
        -o /usr/local/bin/kubectl
    chmod +x /usr/local/bin/kubectl
fi

if command -v helm &>/dev/null; then
    echo "[4/6] Helm already installed: $(helm version --short)"
else
    echo "[4/6] Installing Helm..."
    curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
fi

# ---------------------------------------------------------------------------
# Node.js
# ---------------------------------------------------------------------------

if command -v node &>/dev/null && [ "$(node --version | cut -d. -f1 | tr -d v)" -ge 22 ]; then
    echo "[5/6] Node.js already installed: $(node --version)"
else
    echo "[5/6] Installing Node.js 22..."
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
    apt-get install -y -qq nodejs
fi

# ---------------------------------------------------------------------------
# uv (Python package manager)
# ---------------------------------------------------------------------------

if command -v uv &>/dev/null; then
    echo "[6/6] uv already installed: $(uv --version)"
else
    echo "[6/6] Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Make available to current session
    export PATH="$HOME/.local/bin:$PATH"
fi

echo ""
echo "=== Bootstrap complete ==="
echo ""
echo "The cluster: build each K3s server with scripts/build-k3s-node.sh, run"
echo "as root on that server, and put its kubeconfig where make reads it"
echo "(KUBECONFIG in config.mk, /etc/rancher/k3s/k3s.yaml by default)."
echo ""
echo "Next steps:"
echo "  cd nodal"
echo "  make all"
echo ""
