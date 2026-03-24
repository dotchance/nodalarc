#!/usr/bin/env bash
# na-teardown.sh — Complete NodalArc teardown
# This is the ONLY permitted teardown mechanism. Never use kubectl delete namespace
# as a standalone teardown. Never construct custom teardown sequences.
# This script must be run to completion before any new deploy.

set -euo pipefail
NAMESPACE="nodalarc"
KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
export KUBECONFIG

echo "=== NodalArc Teardown ==="

# Step 1: Delete ConstellationSpec CRs so Operator cleans up session pods
echo "[1/8] Deleting ConstellationSpec resources..."
kubectl delete constellationspec --all -n "$NAMESPACE" \
    --ignore-not-found --timeout=60s || true

# Step 2: Wait for Operator to finish session pod cleanup
# Session pods carry the nodalarc.io/node-id label; platform pods do not.
echo "[2/8] Waiting for session pods to terminate..."
TIMEOUT=120
ELAPSED=0
while true; do
    SESSION_PODS=$(kubectl get pods -n "$NAMESPACE" \
        -l nodalarc.io/node-id \
        --no-headers 2>/dev/null | grep -v Terminating || true)
    if [ -z "$SESSION_PODS" ]; then
        break
    fi
    sleep 5; ELAPSED=$((ELAPSED+5))
    if [ "$ELAPSED" -ge "$TIMEOUT" ]; then
        echo "ERROR: Session pods did not terminate within ${TIMEOUT}s"
        kubectl get pods -n "$NAMESPACE" -l nodalarc.io/node-id
        exit 1
    fi
done

# Step 3: Helm uninstall — removes all Helm-managed resources including DaemonSet
echo "[3/8] Helm uninstall..."
helm uninstall nodalarc -n "$NAMESPACE" \
    --ignore-not-found --timeout=120s || true

# Step 4: Wait for DaemonSet pod to actually terminate
echo "[4/8] Waiting for Node Agent DaemonSet pod to terminate..."
kubectl wait pod -n "$NAMESPACE" \
    -l app=nodalarc-node-agent \
    --for=delete --timeout=60s 2>/dev/null || true

# Step 5: Delete namespace (remaining resources)
echo "[5/8] Deleting namespace..."
kubectl delete namespace "$NAMESPACE" \
    --ignore-not-found --timeout=120s || true

# Step 6: Delete cluster-scoped resources
echo "[6/8] Deleting cluster-scoped resources..."
kubectl delete crd constellationspecs.nodalarc.io \
    --ignore-not-found || true
kubectl delete clusterrole \
    nodalarc-operator nodalarc-orchestrator-cluster \
    nodalarc-node-agent nodalarc-scheduler \
    --ignore-not-found || true
kubectl delete clusterrolebinding \
    nodalarc-operator nodalarc-orchestrator-cluster \
    nodalarc-node-agent nodalarc-scheduler \
    --ignore-not-found || true
# Label-based catch-all (after Task 3 adds labels)
kubectl delete clusterrole,clusterrolebinding \
    -l nodalarc.io/managed-by=helm 2>/dev/null || true

# Step 7: Clean host-side kernel state (belt AND suspenders)
echo "[7/8] Cleaning host-side kernel state..."
# Remove nodalarc veth pairs
ip link show 2>/dev/null | grep -oE '[a-z0-9_]+_isl_[a-z0-9_]+' | \
    xargs -r -I{} ip link del {} 2>/dev/null || true
ip link show 2>/dev/null | grep -oE '[a-z0-9_]+_gnd_[a-z0-9_]+' | \
    xargs -r -I{} ip link del {} 2>/dev/null || true
# Remove nodalarc GS bridge ports
ip link show 2>/dev/null | grep -oE '_gbr-[a-z0-9_]+' | \
    xargs -r -I{} ip link del {} 2>/dev/null || true
# Remove nodalarc ground bridges
ip link show type bridge 2>/dev/null | grep -oE 'br-gnd-[a-z0-9_]+' | \
    xargs -r -I{} ip link del {} 2>/dev/null || true

# Step 8: Verify — nothing should remain
echo "[8/8] Verifying clean state..."
ERRORS=0

# Check no nodalarc pods survive
PODS=$(kubectl get pods -A 2>/dev/null | grep nodalarc | grep -v Terminating || true)
if [ -n "$PODS" ]; then
    echo "ERROR: Nodalarc pods still running:"
    echo "$PODS"
    ERRORS=$((ERRORS+1))
fi

# Check no nodalarc CRD survives
if kubectl get crd constellationspecs.nodalarc.io \
    &>/dev/null 2>&1; then
    echo "ERROR: ConstellationSpec CRD still exists"
    ERRORS=$((ERRORS+1))
fi

# Check no nodalarc kernel state survives
VETHS=$(ip link show 2>/dev/null | \
    grep -E '_isl_|_gnd_|_gbr-|br-gnd-' || true)
if [ -n "$VETHS" ]; then
    echo "ERROR: Nodalarc kernel interfaces still exist:"
    echo "$VETHS"
    ERRORS=$((ERRORS+1))
fi

if [ "$ERRORS" -gt 0 ]; then
    echo ""
    echo "Teardown incomplete. Fix the above before deploying."
    exit 1
fi

echo ""
echo "=== Teardown complete. Cluster is clean. ==="
