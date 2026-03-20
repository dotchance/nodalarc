#!/bin/bash
# Rebuild and redeploy the VS-API container image.
# Usage: sudo scripts/rebuild-vsapi.sh
set -euo pipefail

export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
cd "$(dirname "$0")/.."

echo "=== Step 1: Build image ==="
docker build --no-cache -f vs_api/Dockerfile -t nodalarc/vs-api:latest .
echo ""

echo "=== Step 2: Import into K3s containerd ==="
docker save nodalarc/vs-api:latest | k3s ctr images import -
echo ""

echo "=== Step 3: Delete old pod and wait for termination ==="
kubectl delete pod -n nodalarc -l app=nodalarc-vs-api --wait=true --timeout=60s 2>/dev/null || true
echo ""

echo "=== Step 4: Wait for new pod ==="
for i in $(seq 1 45); do
    status=$(kubectl get pods -n nodalarc -l app=nodalarc-vs-api --no-headers 2>/dev/null | grep Running)
    if [ -n "$status" ]; then
        echo "VS-API pod Running after ${i}s"
        break
    fi
    sleep 2
done

echo ""
echo "=== Step 5: Verify ==="
POD=$(kubectl get pods -n nodalarc -l app=nodalarc-vs-api -o jsonpath='{.items[0].metadata.name}')
echo "Pod: $POD"
kubectl get pod -n nodalarc "$POD" --no-headers
echo ""

# Wait for VS-API to be ready (needs FullStateSnapshot for state)
echo "Waiting 35s for state population..."
sleep 35

# Health check
TOKEN=$(curl -s http://192.168.10.202:8080/api/v1/auth/token 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin).get('token',''))" 2>/dev/null || echo "")
if [ -n "$TOKEN" ]; then
    NODES=$(curl -s http://192.168.10.202:8080/api/v1/state -H "Authorization: Bearer $TOKEN" 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('nodes',[])))" 2>/dev/null || echo "?")
    echo "State: nodes=$NODES"
else
    echo "WARNING: Could not get auth token"
fi

echo ""
echo "=== Done ==="
