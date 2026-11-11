#!/bin/bash
# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
# One-time bootstrap of a NodalArc build machine: the machine make runs on to
# build the images and drive the cluster. Until the images are published, every
# user builds them from a source checkout here.
#
# Installs make, Docker, kubectl, Helm, uv and crane. It touches no kernel
# setting and no cluster: a K3s server is built by scripts/build-k3s-node.sh,
# run on that server, and the Node Agent loads what a session needs.
#
# Idempotent: safe to run more than once. Requires root (or sudo).
#
# After this script completes: build K3s (scripts/build-k3s-node.sh on this
# machine, or scripts/build-k3s-cluster.sh for several servers), then make all.

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

# The downloaded binaries follow the machine's architecture: Kubernetes
# names it amd64 or arm64, crane's releases x86_64 or arm64.
case "$(uname -m)" in
    x86_64) kube_arch=amd64; crane_arch=x86_64 ;;
    aarch64|arm64) kube_arch=arm64; crane_arch=arm64 ;;
    *) echo "ERROR: no kubectl and crane downloads for the $(uname -m) architecture."; exit 1 ;;
esac

# ---------------------------------------------------------------------------
# System packages
# ---------------------------------------------------------------------------

echo "[1/6] Installing system packages..."
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
    curl ca-certificates gnupg lsb-release jq git make \
    iproute2 iptables

# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------

if command -v docker &>/dev/null; then
    echo "[2/6] Docker already installed: $(docker --version)"
else
    echo "[2/6] Installing Docker..."
    curl -fsSL https://get.docker.com | sh
fi

# The image builds pass named build contexts (--build-context), which only the
# buildx builder accepts. Docker's own packages ship it as docker-buildx-plugin,
# Ubuntu's and Debian's docker.io as docker-buildx.
if ! docker buildx version &>/dev/null; then
    if dpkg -s docker-ce &>/dev/null; then
        apt-get install -y -qq docker-buildx-plugin
    elif dpkg -s docker.io &>/dev/null; then
        apt-get install -y -qq docker-buildx
    else
        echo "ERROR: this Docker has no buildx, which NodalArc's image builds need; install the buildx plugin for it."
        exit 1
    fi
fi
echo "  buildx: $(docker buildx version)"

# make runs Docker as the user who ran this script, which needs the docker group.
docker_user="${SUDO_USER:-}"
if [ -n "$docker_user" ] && [ "$docker_user" != root ] && ! id -nG "$docker_user" | tr ' ' '\n' | grep -qx docker; then
    usermod -aG docker "$docker_user"
    echo "  NOTE: $docker_user joined the docker group; log out and back in before the next step."
fi

# ---------------------------------------------------------------------------
# kubectl + Helm
# ---------------------------------------------------------------------------

if command -v kubectl &>/dev/null; then
    echo "[3/6] kubectl already installed: $(kubectl version --client --short 2>/dev/null || kubectl version --client)"
else
    echo "[3/6] Installing kubectl..."
    curl -fsSL "https://dl.k8s.io/release/$(curl -fsSL https://dl.k8s.io/release/stable.txt)/bin/linux/$kube_arch/kubectl" \
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
# uv (Python package manager)
# ---------------------------------------------------------------------------

if command -v uv &>/dev/null; then
    echo "[5/6] uv already installed: $(uv --version)"
else
    echo "[5/6] Installing uv..."
    # Into /usr/local/bin, beside kubectl and Helm: run under sudo, the
    # installer's default is root's home, where the user's shell never looks.
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
fi

# ---------------------------------------------------------------------------
# crane (removes NodalArc's images from a registry: make nuke, make reset-platform)
# ---------------------------------------------------------------------------

CRANE_VERSION=v0.22.1
if command -v crane &>/dev/null; then
    echo "[6/6] crane already installed: $(crane version)"
else
    echo "[6/6] Installing crane $CRANE_VERSION..."
    crane_dir="$(mktemp -d)"
    if [ -z "$crane_dir" ] || [ ! -d "$crane_dir" ]; then
        echo "ERROR: could not create a directory for the crane download"
        exit 1
    fi
    crane_base="https://github.com/google/go-containerregistry/releases/download/$CRANE_VERSION"
    crane_archive="go-containerregistry_Linux_$crane_arch.tar.gz"
    curl -fsSL -o "$crane_dir/$crane_archive" "$crane_base/$crane_archive"
    curl -fsSL -o "$crane_dir/checksums.txt" "$crane_base/checksums.txt"
    crane_expected="$(awk -v f="$crane_archive" '$2 == f {print $1}' "$crane_dir/checksums.txt")"
    crane_actual="$(sha256sum "$crane_dir/$crane_archive" | awk '{print $1}')"
    if [ -z "$crane_expected" ] || [ "$crane_expected" != "$crane_actual" ]; then
        echo "ERROR: $crane_archive does not match the checksum its release publishes"
        rm -rf -- "$crane_dir"
        exit 1
    fi
    tar -xzf "$crane_dir/$crane_archive" -C "$crane_dir" crane
    install -m 755 "$crane_dir/crane" /usr/local/bin/crane
    rm -rf -- "$crane_dir"
fi

echo ""
echo "=== Bootstrap complete ==="
echo ""
echo "Next, in the NodalArc checkout, build K3s on this machine:"
echo "  sudo scripts/build-k3s-node.sh"
echo "or a cluster of several servers from this machine (their ssh addresses, the first one first):"
echo "  scripts/build-k3s-cluster.sh <first server> <server> ..."
echo "then:"
echo "  make all"
echo ""
