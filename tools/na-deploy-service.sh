#!/usr/bin/env bash
# Build remains a Make dependency; this script loads one image and restarts one resource.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-nodalarc}"
SUDO_CTR="${SUDO_CTR:-sudo}"
if [ -n "$SUDO_CTR" ]; then
    read -r -a SUDO_CTR_CMD <<< "$SUDO_CTR"
else
    SUDO_CTR_CMD=()
fi

if [ "$#" -ne 2 ]; then
    echo "usage: na-deploy-service.sh IMAGE_LOGICAL_NAME K8S_RESOURCE" >&2
    exit 2
fi

logical_name="$1"
resource="$2"
image="$(bash "$ROOT_DIR/tools/na-images.sh" image-for "$logical_name")"
record="$(bash "$ROOT_DIR/tools/na-mode.sh")"
IFS=$'\t' read -r MODE_RESOLVED REGISTRY_HOST_RESOLVED REGISTRY_PREFIX_RESOLVED NODE_COUNT MIRROR_THIRD_PARTY_RESOLVED <<< "$record"

if ! kubectl get "$resource" -n "$NAMESPACE" >/dev/null 2>&1; then
    echo "[deploy] ERROR: resource does not exist: $resource in namespace $NAMESPACE" >&2
    exit 1
fi

if ! docker image inspect "$image" >/dev/null 2>&1; then
    echo "[deploy] ERROR: local image is missing: $image" >&2
    exit 1
fi

echo "[deploy] Loading $image..."
if [ "$MODE_RESOLVED" = "single-node" ]; then
    docker save "$image" | "${SUDO_CTR_CMD[@]}" k3s ctr images import -
else
    docker push "$image"
fi

echo "[deploy] Restarting $resource..."
kubectl rollout restart "$resource" -n "$NAMESPACE"
kubectl rollout status "$resource" -n "$NAMESPACE" --timeout=60s
echo "[deploy] Next: make status"
