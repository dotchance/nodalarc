#!/usr/bin/env bash
# Implement make load for single-node containerd import and multi-node registry push.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUDO_CTR="${SUDO_CTR:-sudo}"
NAMESPACE="${NAMESPACE:-nodalarc}"
HELM_RELEASE="${HELM_RELEASE:-nodalarc}"
KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
export KUBECONFIG
if [ -n "$SUDO_CTR" ]; then
    read -r -a SUDO_CTR_CMD <<< "$SUDO_CTR"
else
    SUDO_CTR_CMD=()
fi

record="$(bash "$ROOT_DIR/scripts/na-mode.sh")"
IFS=$'\t' read -r MODE_RESOLVED REGISTRY_HOST_RESOLVED REGISTRY_PREFIX_RESOLVED NODE_COUNT MIRROR_THIRD_PARTY_RESOLVED <<< "$record"

ensure_local_image() {
    local image="$1"
    if ! docker image inspect "$image" >/dev/null 2>&1; then
        echo "[load] ERROR: required local image is missing: $image" >&2
        echo "[load] Run: make build" >&2
        exit 1
    fi
}

import_image() {
    local image="$1"
    echo "  import $image"
    docker save "$image" | "${SUDO_CTR_CMD[@]}" k3s ctr images import -
}

push_image() {
    local image="$1"
    echo "  push $image"
    docker push "$image"
}

print_next_step() {
    if helm status "$HELM_RELEASE" -n "$NAMESPACE" >/dev/null 2>&1; then
        echo "[load] Next: make upgrade, or make reinstall && make session for a destructive platform refresh."
    elif kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
        echo "[load] Next: make teardown or make reinstall; make install will refuse the existing namespace."
    else
        echo "[load] Next: make install"
    fi
}

if [ "$MODE_RESOLVED" = "single-node" ]; then
    echo "[load] Importing runtime images into local K3s containerd..."
    while IFS=$'\t' read -r scope kind name image required source; do
        [ "$required" = "required" ] || continue
        if [ "$kind" = "nodalarc" ]; then
            ensure_local_image "$image"
        else
            docker image inspect "$image" >/dev/null 2>&1 || docker pull "$image"
        fi
        import_image "$image"
    done < <(bash "$ROOT_DIR/scripts/na-images.sh" list-all-runtime-images)
else
    echo "[load] Pushing NodalArc runtime images to $REGISTRY_HOST_RESOLVED..."
    if ! curl -sf --max-time 5 "http://$REGISTRY_HOST_RESOLVED/v2/" >/dev/null 2>&1; then
        echo "[load] ERROR: registry is not reachable: $REGISTRY_HOST_RESOLVED" >&2
        exit 1
    fi
    while IFS=$'\t' read -r scope kind name image required source; do
        [ "$required" = "required" ] || continue
        [ "$kind" = "nodalarc" ] || continue
        ensure_local_image "$image"
        push_image "$image"
    done < <(bash "$ROOT_DIR/scripts/na-images.sh" list-nodalarc-runtime-images)
    echo "[load] Third-party runtime images remain upstream references in current multi-node mode."
fi

echo "[load] Done."
print_next_step
