#!/usr/bin/env bash
# Start or replace the current NodalArc session.

set -euo pipefail

NAMESPACE="${NAMESPACE:-nodalarc}"
DEFAULT_SESSION="${DEFAULT_SESSION:-configs/sessions/demo-36-ospf.yaml}"
KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
export KUBECONFIG

if ! kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
    echo "[session] ERROR: namespace $NAMESPACE does not exist. Run: make install" >&2
    exit 1
fi

echo "[session] Starting: $DEFAULT_SESSION"
echo "[session] Waiting for CRD (timeout 60s)..."
waited=0
while ! kubectl get crd constellationspecs.nodalarc.io >/dev/null 2>&1; do
    sleep 2
    waited=$((waited + 2))
    printf '\r[session]   Waiting for Operator to register CRD... (%ss)' "$waited"
    if [ "$waited" -ge 60 ]; then
        echo ""
        echo "[session] ERROR: CRD not registered after 60s. Is the Operator running?" >&2
        exit 1
    fi
done
if [ "$waited" -gt 0 ]; then
    echo ""
fi

tmp_file="$(mktemp)"
trap 'rm -f "$tmp_file"' EXIT
{
    printf 'apiVersion: nodalarc.io/v1alpha1\n'
    printf 'kind: ConstellationSpec\n'
    printf 'metadata:\n'
    printf '  name: current-session\n'
    printf '  namespace: %s\n' "$NAMESPACE"
    printf 'spec:\n'
    printf '  sessionYaml: |\n'
    sed 's/^/    /' "$DEFAULT_SESSION"
} > "$tmp_file"
kubectl apply -f "$tmp_file"

echo "[session] Waiting for Ready (timeout 300s)..."
elapsed=0
while [ "$elapsed" -lt 300 ]; do
    phase="$(kubectl get constellationspec current-session -n "$NAMESPACE" -o jsonpath='{.status.phase}' 2>/dev/null || echo Unknown)"
    if [ "$phase" = "Ready" ]; then
        echo ""
        pods="$(kubectl get pods -n "$NAMESPACE" -l nodalarc.io/node-id --no-headers 2>/dev/null | wc -l | tr -d ' ')"
        running="$(kubectl get pods -n "$NAMESPACE" -l nodalarc.io/node-id --no-headers 2>/dev/null | grep -c Running || true)"
        not_running="$(kubectl get pods -n "$NAMESPACE" -l nodalarc.io/node-id --no-headers 2>/dev/null | grep -v Running | grep -v Completed || true)"
        if [ -n "$not_running" ]; then
            echo "[session] ERROR: Phase is Ready but some session pods are not running:" >&2
            echo "$not_running" >&2
            exit 1
        fi
        echo "[session] Session ready. $running/$pods session pods running."
        echo "[session] Next: make status"
        exit 0
    fi
    if [ "$phase" = "Error" ]; then
        echo ""
        msg="$(kubectl get constellationspec current-session -n "$NAMESPACE" -o jsonpath='{.status.message}' 2>/dev/null || true)"
        echo "[session] ERROR: $msg" >&2
        exit 1
    fi
    sleep 5
    elapsed=$((elapsed + 5))
    pods="$(kubectl get pods -n "$NAMESPACE" -l nodalarc.io/node-id --no-headers 2>/dev/null | grep -c Running || true)"
    printf '\r[session]   Phase: %s, %s session pods running (%ss/300s)' "$phase" "$pods" "$elapsed"
done

echo ""
echo "[session] ERROR: timed out after 300s" >&2
exit 1
