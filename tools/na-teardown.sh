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
echo "[1/9] Deleting ConstellationSpec resources..."
kubectl delete constellationspec --all -n "$NAMESPACE" \
    --ignore-not-found --timeout=60s || true

# Step 2: Wait for Operator to finish session pod cleanup
# Session pods carry the nodalarc.io/node-id label; platform pods do not.
echo "[2/9] Waiting for session pods to terminate..."
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

# Step 3: Clean host-side kernel state on ALL nodes via Node Agent DaemonSet
# The Node Agent runs with hostNetwork on every node — kubectl exec operates
# on the host's network namespace. Clean VXLAN, veth, and bridge interfaces
# BEFORE Helm uninstall deletes the DaemonSet pods.
echo "[3/9] Cleaning host-side kernel state via Node Agent pods..."
CLEANUP_SCRIPT='
ip link show 2>/dev/null | grep -oE "vx[0-9]{5}" | xargs -r -I{} ip link del {} 2>/dev/null
ip link show 2>/dev/null | grep -oE "vh[0-9]{5}" | xargs -r -I{} ip link del {} 2>/dev/null
ip link show 2>/dev/null | grep -oE "[a-z0-9_]+_isl_[a-z0-9_]+" | xargs -r -I{} ip link del {} 2>/dev/null
ip link show 2>/dev/null | grep -oE "[a-z0-9_]+_gnd_[a-z0-9_]+" | xargs -r -I{} ip link del {} 2>/dev/null
ip link show 2>/dev/null | grep -oE "_gbr-[a-z0-9_]+" | xargs -r -I{} ip link del {} 2>/dev/null
ip link show type bridge 2>/dev/null | grep -oE "br-gnd-[a-z0-9_]+" | xargs -r -I{} ip link del {} 2>/dev/null
echo done
'
NA_PODS=$(kubectl get pods -n "$NAMESPACE" -l app=nodalarc-node-agent \
    --no-headers -o custom-columns=NAME:.metadata.name,NODE:.spec.nodeName 2>/dev/null || true)
if [ -n "$NA_PODS" ]; then
    while IFS= read -r line; do
        POD_NAME=$(echo "$line" | awk '{print $1}')
        NODE_NAME=$(echo "$line" | awk '{print $2}')
        echo "  Cleaning $NODE_NAME via $POD_NAME..."
        kubectl exec "$POD_NAME" -n "$NAMESPACE" -c node-agent -- \
            sh -c "$CLEANUP_SCRIPT" 2>/dev/null || \
            echo "  WARNING: exec failed on $POD_NAME (non-fatal)"
    done <<< "$NA_PODS"
else
    echo "  No Node Agent pods found — cleaning local host only"
fi

# Local cleanup (belt and suspenders — also covers the control plane node
# in case no Node Agent pod was scheduled here)
ip link show 2>/dev/null | grep -oE 'vx[0-9]{5}' | \
    xargs -r -I{} ip link del {} 2>/dev/null || true
ip link show 2>/dev/null | grep -oE 'vh[0-9]{5}' | \
    xargs -r -I{} ip link del {} 2>/dev/null || true
ip link show 2>/dev/null | grep -oE '[a-z0-9_]+_isl_[a-z0-9_]+' | \
    xargs -r -I{} ip link del {} 2>/dev/null || true
ip link show 2>/dev/null | grep -oE '[a-z0-9_]+_gnd_[a-z0-9_]+' | \
    xargs -r -I{} ip link del {} 2>/dev/null || true
ip link show 2>/dev/null | grep -oE '_gbr-[a-z0-9_]+' | \
    xargs -r -I{} ip link del {} 2>/dev/null || true
ip link show type bridge 2>/dev/null | grep -oE 'br-gnd-[a-z0-9_]+' | \
    xargs -r -I{} ip link del {} 2>/dev/null || true

# Step 4: Helm uninstall — removes all Helm-managed resources including DaemonSet
echo "[4/9] Helm uninstall..."
helm uninstall nodalarc -n "$NAMESPACE" \
    --ignore-not-found --timeout=120s || true

# Step 5: Wait for DaemonSet pod to actually terminate
echo "[5/9] Waiting for Node Agent DaemonSet pod to terminate..."
kubectl wait pod -n "$NAMESPACE" \
    -l app=nodalarc-node-agent \
    --for=delete --timeout=60s 2>/dev/null || true

# Step 6: Delete namespace (remaining resources)
echo "[6/9] Deleting namespace..."
kubectl delete namespace "$NAMESPACE" \
    --ignore-not-found --timeout=120s || true

# Step 7: Delete cluster-scoped resources
echo "[7/9] Deleting cluster-scoped resources..."
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

# Step 8: Local kernel state catch-all (in case Step 3 exec failed)
echo "[8/9] Final local kernel state cleanup..."
ip link show 2>/dev/null | grep -oE 'vx[0-9]{5}' | \
    xargs -r -I{} ip link del {} 2>/dev/null || true
ip link show 2>/dev/null | grep -oE 'vh[0-9]{5}' | \
    xargs -r -I{} ip link del {} 2>/dev/null || true
ip link show 2>/dev/null | grep -oE '[a-z0-9_]+_isl_[a-z0-9_]+' | \
    xargs -r -I{} ip link del {} 2>/dev/null || true
ip link show 2>/dev/null | grep -oE '[a-z0-9_]+_gnd_[a-z0-9_]+' | \
    xargs -r -I{} ip link del {} 2>/dev/null || true
ip link show 2>/dev/null | grep -oE '_gbr-[a-z0-9_]+' | \
    xargs -r -I{} ip link del {} 2>/dev/null || true
ip link show type bridge 2>/dev/null | grep -oE 'br-gnd-[a-z0-9_]+' | \
    xargs -r -I{} ip link del {} 2>/dev/null || true

# Step 9: Verify — nothing should remain
echo "[9/9] Verifying clean state..."
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

# Check no nodalarc kernel state survives (local node)
VETHS=$(ip link show 2>/dev/null | \
    grep -E '_isl_|_gnd_|_gbr-|br-gnd-|vx[0-9]{5}|vh[0-9]{5}' || true)
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
