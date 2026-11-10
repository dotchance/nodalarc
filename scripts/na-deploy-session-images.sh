#!/usr/bin/env bash
# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
# Deploy the session images: push every session image the tree built, move
# the Operator's session image map to them, wait for the Operator to roll,
# then prove the Operator holds this tree's map.
#
# Session pods do not run the platform services' images. The shipped profiles
# name FRR, base and the stack images by placeholder, and the Operator swaps
# each placeholder for the image its map names (WORKLOAD_DEV_IMAGE_OVERRIDES,
# written from the Helm value workloadDevImageOverrides). make deploy-all runs
# this after the platform services, so the next session starts from this
# tree's session images.
#
# Build remains a Make dependency; this script transports, moves the map,
# converges and verifies.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-nodalarc}"
# shellcheck source=scripts/na-lib.sh
. "$ROOT_DIR/scripts/na-lib.sh"
LIB_PREFIX="deploy:session-images"
OPERATOR="deployment/nodalarc-operator"

HELM_CHART="deploy/helm"
# The chart ships Chart.yaml.in; render it the same way install/upgrade do.
if [ -f "$ROOT_DIR/$HELM_CHART/Chart.yaml.in" ] || [ -f "$HELM_CHART/Chart.yaml.in" ]; then
    PROJECT_VERSION="${PROJECT_VERSION:-$(bash "$ROOT_DIR/scripts/na-project-version.sh")}"
    HELM_CHART="$(PROJECT_VERSION="$PROJECT_VERSION" bash "$ROOT_DIR/scripts/na-render-helm-chart.sh" "$HELM_CHART")"
fi
SUDO_CTR="${SUDO_CTR:-sudo}"
if [ -n "$SUDO_CTR" ]; then
    read -r -a SUDO_CTR_CMD <<< "$SUDO_CTR"
else
    SUDO_CTR_CMD=()
fi

mode_record_load "$(bash "$ROOT_DIR/scripts/na-mode.sh")"

if ! kubectl get "$OPERATOR" -n "$NAMESPACE" >/dev/null 2>&1; then
    echo "[deploy:session-images] ERROR: $OPERATOR does not exist in namespace $NAMESPACE." >&2
    echo "[deploy:session-images] Next: make install (no platform yet) or check NAMESPACE." >&2
    exit 1
fi

# The same rule as a single-service deploy: values move here, never the chart.
if ! release_chart_matches "$NAMESPACE" "$HELM_RELEASE_NAME" "$HELM_CHART"; then
    echo "[deploy:session-images] ERROR: the chart differs from the deployed release; a deploy cannot carry chart changes." >&2
    printf '%s\n' "$RELEASE_CHART_DIFF" | sed 's/^/  /' >&2
    echo "[deploy:session-images] Next: make build && make load && make upgrade (every image moves with the chart)." >&2
    exit 1
fi

work="$(mktemp -d)"
if [ -z "$work" ] || [ ! -d "$work" ]; then
    echo "[deploy:session-images] ERROR: could not create a work directory." >&2
    exit 1
fi
trap 'rm -rf -- "$work"' EXIT

# --- 1. Transport -----------------------------------------------------------
if ! listing="$(bash "$ROOT_DIR/scripts/na-images.sh" list-session-runtime-images)" || [ -z "$listing" ]; then
    echo "[deploy:session-images] ERROR: the image inventory named no session image." >&2
    exit 1
fi
while IFS=$'\t' read -r _ kind name image _ _; do
    [ "$kind" = "nodalarc" ] || continue
    if ! docker image inspect "$image" >/dev/null 2>&1; then
        echo "[deploy:session-images] ERROR: local image is missing: $image" >&2
        echo "[deploy:session-images] Next: make build-base-images (or make build)." >&2
        exit 1
    fi
    echo "[deploy:session-images] Pushing $name ($image)..."
    if [ "$MODE_RESOLVED" = "single-node" ]; then
        docker save "$image" | "${SUDO_CTR_CMD[@]}" k3s ctr images import - >/dev/null
    else
        docker push "$image" >/dev/null
    fi
done <<< "$listing"

# --- 2. Move the Operator's session image map --------------------------------
# The map names each image by registry digest (by tag in single-node mode), so
# it is computed after the push. It travels as a values file: helm --set would
# read its braces and commas as list syntax.
bash "$ROOT_DIR/scripts/na-images.sh" workload-dev-overrides > "$work/map.json"
bash "$ROOT_DIR/scripts/na-images.sh" workload-dev-overrides-values > "$work/values.yaml"
echo "[deploy:session-images] Setting the Operator's session image map..."
helm upgrade "$HELM_RELEASE_NAME" "$HELM_CHART" --namespace "$NAMESPACE" \
    --reuse-values --values "$work/values.yaml" >/dev/null
echo "[deploy:session-images] Helm release updated."

# --- 3. Converge ---------------------------------------------------------------
echo "[deploy:session-images] Waiting for rollout of $OPERATOR..."
if ! kubectl rollout status "$OPERATOR" -n "$NAMESPACE" --timeout=180s >/dev/null; then
    echo "[deploy:session-images] ERROR: $OPERATOR did not roll out within 180s." >&2
    kubectl get "$OPERATOR" -n "$NAMESPACE" >&2
    echo "[deploy:session-images] Next: kubectl describe $OPERATOR -n $NAMESPACE; make status." >&2
    exit 1
fi

# --- 4. Prove it -------------------------------------------------------------
deployed_map="$(kubectl get "$OPERATOR" -n "$NAMESPACE" \
    -o jsonpath='{.spec.template.spec.containers[?(@.name=="operator")].env[?(@.name=="WORKLOAD_DEV_IMAGE_OVERRIDES")].value}')"
if ! python3 -c 'import json, sys; sys.exit(json.load(open(sys.argv[1])) != json.loads(sys.argv[2]))' \
    "$work/map.json" "$deployed_map"; then
    echo "[deploy:session-images] ERROR: the Operator's session image map is not this tree's." >&2
    echo "  expected: $(cat "$work/map.json")" >&2
    echo "  deployed: $deployed_map" >&2
    exit 1
fi
echo "[deploy:session-images] VERIFIED: the Operator maps every session image to this tree's build."
echo "[deploy:session-images] Done. Next: make session"
