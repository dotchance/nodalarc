#!/bin/bash
# K3s single-host bootstrap for Nodal Arc.
# Thin wrapper — verifies K3s is running and creates prerequisites.
set -euo pipefail

echo "=== Nodal Arc K3s Setup ==="

# Verify K3s is running
if ! systemctl is-active --quiet k3s; then
    echo "ERROR: K3s is not running. Start with: sudo systemctl start k3s"
    exit 1
fi

# Verify kubectl access
if ! kubectl cluster-info &>/dev/null; then
    echo "ERROR: Cannot reach K3s API. Check KUBECONFIG."
    exit 1
fi

echo "K3s cluster: $(kubectl cluster-info | head -1)"

# Create namespace if it doesn't exist
if ! kubectl get namespace nodalarc &>/dev/null; then
    kubectl create namespace nodalarc
    echo "Created namespace: nodalarc"
else
    echo "Namespace nodalarc already exists"
fi

# Verify crictl is available
if ! command -v crictl &>/dev/null; then
    echo "WARNING: crictl not found. Required for container PID discovery."
    echo "Install: https://github.com/kubernetes-sigs/cri-tools"
fi

echo "=== K3s setup complete ==="
