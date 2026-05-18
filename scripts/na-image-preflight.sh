#!/usr/bin/env bash
# Verify runtime images are available before Helm creates pods.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUDO_CTR="${SUDO_CTR:-sudo}"
if [ -n "$SUDO_CTR" ]; then
    read -r -a SUDO_CTR_CMD <<< "$SUDO_CTR"
else
    SUDO_CTR_CMD=()
fi

record="$(bash "$ROOT_DIR/scripts/na-mode.sh")"
IFS=$'\t' read -r MODE_RESOLVED REGISTRY_HOST_RESOLVED REGISTRY_PREFIX_RESOLVED NODE_COUNT MIRROR_THIRD_PARTY_RESOLVED <<< "$record"

image_available_in_containerd() {
    local image="$1"
    local suffix="${image#docker.io/}"
    "${SUDO_CTR_CMD[@]}" k3s ctr images ls -q 2>/dev/null | awk -v img="$suffix" '
        $0 == img || $0 == "docker.io/" img || index($0, "/" img) { found = 1 }
        END { exit found ? 0 : 1 }
    '
}

image_available_in_registry() {
    local image="$1"
    local without_host repo tag
    local accept
    without_host="${image#"$REGISTRY_HOST_RESOLVED"/}"
    repo="${without_host%:*}"
    tag="${without_host##*:}"
    accept="application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json"
    curl -sf --max-time 5 -H "Accept: $accept" \
        "http://$REGISTRY_HOST_RESOLVED/v2/$repo/manifests/$tag" >/dev/null 2>&1
}

missing=0

if [ "$MODE_RESOLVED" = "single-node" ]; then
    while IFS=$'\t' read -r scope kind name image required source; do
        [ "$required" = "required" ] || continue
        if ! image_available_in_containerd "$image"; then
            echo "[preflight] missing from K3s containerd: $image" >&2
            missing=1
        fi
    done < <(bash "$ROOT_DIR/scripts/na-images.sh" list-all-runtime-images)
else
    if ! curl -sf --max-time 5 "http://$REGISTRY_HOST_RESOLVED/v2/" >/dev/null 2>&1; then
        echo "[preflight] registry not reachable: $REGISTRY_HOST_RESOLVED" >&2
        exit 1
    fi
    while IFS=$'\t' read -r scope kind name image required source; do
        [ "$required" = "required" ] || continue
        [ "$kind" = "nodalarc" ] || continue
        if ! image_available_in_registry "$image"; then
            echo "[preflight] missing from registry: $image" >&2
            missing=1
        fi
    done < <(bash "$ROOT_DIR/scripts/na-images.sh" list-nodalarc-runtime-images)
fi

if [ "$missing" -ne 0 ]; then
    echo "[preflight] Required runtime images are not loaded. Run: make load" >&2
    exit 1
fi

echo "[preflight] Required runtime images are available for $MODE_RESOLVED."
