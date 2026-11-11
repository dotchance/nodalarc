#!/usr/bin/env bash
# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
# Verify runtime images are available before Helm creates pods.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUDO_CTR="${SUDO_CTR:-sudo}"
if [ -n "$SUDO_CTR" ]; then
    read -r -a SUDO_CTR_CMD <<< "$SUDO_CTR"
else
    SUDO_CTR_CMD=()
fi

# shellcheck source=scripts/na-lib.sh
. "$ROOT_DIR/scripts/na-lib.sh"
LIB_PREFIX="preflight"
mode_record_load "$(bash "$ROOT_DIR/scripts/na-mode.sh")"

# The kubelet asks K3s's runtime for each image by its reference; this asks the
# same question, so the answer holds for a tag and for a digest-pinned image.
image_available_in_containerd() {
    "${SUDO_CTR_CMD[@]}" k3s crictl inspecti -q "$1" >/dev/null 2>&1
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
