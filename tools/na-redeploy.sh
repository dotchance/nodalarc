#!/bin/bash
# Copyright 2024-2026 .chance (dotchance)
# Tear down and redeploy a Nodal Arc session via the K8s Operator.
# Usage: na-redeploy.sh --session <path-to-session.yaml>
#
# Tears down any existing session, ensures Helm chart is installed,
# applies the session YAML as a ConstellationSpec CRD, and waits
# for the Operator to reach Ready state.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
NAMESPACE="nodalarc"
TIMEOUT=300

SESSION=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --session) SESSION="$2"; shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

if [ -z "$SESSION" ]; then
    echo "ERROR: --session <path> is required"
    echo "Usage: na-redeploy.sh --session configs/sessions/starlink-176-isis-te.yaml"
    exit 1
fi

if [ ! -f "$SESSION" ]; then
    echo "ERROR: Session file not found: $SESSION"
    exit 1
fi

echo "=== Redeploy: $SESSION ==="
START_TIME=$SECONDS

# Step 1: Teardown existing session (if any)
if kubectl get namespace "$NAMESPACE" &>/dev/null; then
    echo "[1/4] Tearing down existing session..."
    "$SCRIPT_DIR/na-teardown.sh"
    echo "Waiting 2s for port release..."
    sleep 2
else
    echo "[1/4] No existing session — skipping teardown"
fi

# Step 2: Ensure Helm chart is installed
echo "[2/4] Ensuring Helm chart is installed..."
if ! kubectl get namespace "$NAMESPACE" &>/dev/null; then
    helm install nodalarc deploy/helm \
        --namespace "$NAMESPACE" --create-namespace
    echo "Waiting for platform pods..."
    kubectl wait --for=condition=Ready pod \
        -l app=nodalarc-nats -n "$NAMESPACE" --timeout=60s 2>/dev/null || true
    kubectl wait --for=condition=Ready pod \
        -l app=nodalarc-operator -n "$NAMESPACE" --timeout=60s 2>/dev/null || true
    sleep 5
else
    echo "Helm chart already installed"
fi

# Step 3: Apply session as ConstellationSpec CRD
echo "[3/4] Applying session: $SESSION"
SESSION_YAML=$(cat "$SESSION")

kubectl apply -f - <<EOF
apiVersion: nodalarc.io/v1alpha1
kind: ConstellationSpec
metadata:
  name: current-session
  namespace: $NAMESPACE
spec:
  sessionYaml: |
$(echo "$SESSION_YAML" | sed 's/^/    /')
EOF

# Step 4: Wait for Operator to reach Ready
echo "[4/4] Waiting for session to reach Ready (timeout: ${TIMEOUT}s)..."
ELAPSED=0
while [ $ELAPSED -lt $TIMEOUT ]; do
    PHASE=$(kubectl get constellationspec current-session \
        -n "$NAMESPACE" -o jsonpath='{.status.phase}' 2>/dev/null || echo "Unknown")

    if [ "$PHASE" = "Ready" ]; then
        PODS=$(kubectl get pods -n "$NAMESPACE" --no-headers 2>/dev/null | wc -l)
        RUNNING=$(kubectl get pods -n "$NAMESPACE" --no-headers 2>/dev/null | grep -c Running || true)
        TOTAL_TIME=$((SECONDS - START_TIME))
        echo ""
        echo "=== Redeploy complete in ${TOTAL_TIME}s ==="
        echo "    Session: $SESSION"
        echo "    Phase: $PHASE"
        echo "    Pods: $RUNNING/$PODS Running"
        exit 0
    fi

    if [ "$PHASE" = "Error" ]; then
        MSG=$(kubectl get constellationspec current-session \
            -n "$NAMESPACE" -o jsonpath='{.status.message}' 2>/dev/null)
        echo "ERROR: Operator reported error: $MSG"
        exit 1
    fi

    # Progress indicator every 15s
    if [ $((ELAPSED % 15)) -eq 0 ] && [ $ELAPSED -gt 0 ]; then
        PODS=$(kubectl get pods -n "$NAMESPACE" --no-headers 2>/dev/null | grep -c Running || true)
        echo "  Phase: $PHASE, Running pods: $PODS (${ELAPSED}s elapsed)"
    fi

    sleep 5
    ELAPSED=$((ELAPSED + 5))
done

echo "ERROR: Timed out waiting for Ready after ${TIMEOUT}s (phase: $PHASE)"
exit 1
