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
