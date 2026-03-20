#!/bin/bash
# Rebuild and import all Nodal Arc container images into K3s.
# Does NOT restart pods or redeploy — use na-redeploy.sh for that.
# Usage: sudo scripts/rebuild-all.sh
set -euo pipefail

cd "$(dirname "$0")/.."

for component in ome scheduler vs_api node_agent; do
    dockerfile="${component}/Dockerfile"
    if [ "$component" = "vs_api" ]; then
        tag="nodalarc/vs-api:latest"
    elif [ "$component" = "node_agent" ]; then
        tag="nodalarc/node-agent:latest"
    else
        tag="nodalarc/${component}:latest"
    fi
    echo "=== Building $tag ==="
    docker build --no-cache -f "$dockerfile" -t "$tag" .
    docker save "$tag" | k3s ctr images import -
    echo ""
done

echo "=== Building VF ==="
docker build --no-cache -t nodalarc/vf:latest visualization/
docker save nodalarc/vf:latest | k3s ctr images import -

echo ""
echo "=== All images built and imported ==="
k3s ctr images ls | grep nodalarc | awk '{print $1}' | sort

echo ""
echo "=== Restarting OME + VS-API for integration gate ==="
export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
kubectl delete pod -n nodalarc -l app=nodalarc-ome --wait=true --timeout=60s 2>/dev/null || true
kubectl delete pod -n nodalarc -l app=nodalarc-vs-api --wait=true --timeout=60s 2>/dev/null || true

echo "Waiting for pods to restart..."
for i in $(seq 1 60); do
    running=$(kubectl get pods -n nodalarc --no-headers 2>/dev/null | grep -cE '(ome-|nodalarc-vs-api).*Running' || true)
    if [ "$running" -ge 2 ]; then
        echo "OME + VS-API Running after ${i}s"
        break
    fi
    sleep 2
done

echo "Waiting 35s for OME window computation + state population..."
sleep 35

echo ""
echo "=== Integration tests (mandatory gate) ==="
cd "$(dirname "$0")/.."
if .venv/bin/pytest tests/integration/test_zmq_reliability.py tests/integration/test_satellite_motion.py -v; then
    echo "REBUILD OK — satellites moving"
else
    echo "REBUILD FAILED — satellites not moving. Do not proceed."
    exit 1
fi
