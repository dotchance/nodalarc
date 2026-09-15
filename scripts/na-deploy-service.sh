#!/usr/bin/env bash
# Deploy ONE service: push the image, move the Helm-owned image reference,
# wait for honest convergence, then PROVE the cluster runs what was pushed.
#
# Why Helm and not `rollout restart`: the Deployment's image tag is owned by
# Helm. A restart re-pulls whatever tag the spec already pins — so after any
# commit (tag moved), restart-based deploys silently redeployed the OLD
# image while reporting success. Deploys must move the reference, not bounce
# pods and hope.
#
# Build remains a Make dependency; this script transports + converges + verifies.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-nodalarc}"
# shellcheck source=scripts/na-lib.sh
. "$ROOT_DIR/scripts/na-lib.sh"
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

if [ "$#" -ne 2 ]; then
    echo "usage: na-deploy-service.sh IMAGE_LOGICAL_NAME K8S_RESOURCE" >&2
    exit 2
fi

logical_name="$1"
resource="$2"

LIB_PREFIX="deploy:$logical_name"

# The inventory owns the Helm key and the Kubernetes resource of every
# service. The resource argument still arrives from the Makefile target;
# until that argument goes, it must agree with the owner.
owned_resource="$(bash "$ROOT_DIR/scripts/na-images.sh" resource-for "$logical_name")"
if [ "$resource" != "$owned_resource" ]; then
    echo "[deploy:$logical_name] ERROR: resource argument '$resource' disagrees with the inventory's '$owned_resource'" >&2
    exit 2
fi
helm_key="$(bash "$ROOT_DIR/scripts/na-images.sh" helm-key-for "$logical_name")"
image="$(bash "$ROOT_DIR/scripts/na-images.sh" image-for "$logical_name")"
mode_record_load "$(bash "$ROOT_DIR/scripts/na-mode.sh")"

if ! kubectl get "$resource" -n "$NAMESPACE" >/dev/null 2>&1; then
    echo "[deploy:$logical_name] ERROR: resource does not exist: $resource in namespace $NAMESPACE" >&2
    echo "[deploy:$logical_name] Next: make install (no platform yet) or check NAMESPACE." >&2
    exit 1
fi

# A single-service deploy moves one image and nothing else. The assembled
# chart carries the shared configuration every platform pod mounts; if it
# differs from the release's chart, applying it would roll untouched pods
# onto configuration their images were not built for. That change goes
# through make upgrade, which moves every image with it.
if ! release_chart_matches "$NAMESPACE" "$HELM_RELEASE_NAME" "$HELM_CHART"; then
    echo "[deploy:$logical_name] ERROR: the chart differs from the deployed release; a single-service deploy cannot carry chart changes." >&2
    printf '%s\n' "$RELEASE_CHART_DIFF" | sed 's/^/  /' >&2
    echo "[deploy:$logical_name] Next: make build && make load && make upgrade (every image moves with the chart)." >&2
    exit 1
fi

if ! docker image inspect "$image" >/dev/null 2>&1; then
    echo "[deploy:$logical_name] ERROR: local image is missing: $image" >&2
    echo "[deploy:$logical_name] Next: make build-$logical_name (or make build)." >&2
    exit 1
fi

# --- 1. Transport -----------------------------------------------------------
echo "[deploy:$logical_name] Pushing $image..."
if [ "$MODE_RESOLVED" = "single-node" ]; then
    docker save "$image" | "${SUDO_CTR_CMD[@]}" k3s ctr images import -
    pushed_digest=""
else
    docker push "$image" >/dev/null
    # Strip only the TAG (text after the LAST colon): the registry host:port
    # also contains a colon, so %%:* would truncate to the hostname.
    repo="${image%:*}"
    pushed_digest="$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$image" \
        | grep -F "${repo}@" | head -1 | cut -d@ -f2 || true)"
    if [ -z "$pushed_digest" ]; then
        echo "[deploy:$logical_name] ERROR: pushed image has no registry digest for $repo." >&2
        echo "[deploy:$logical_name] Next: docker image inspect $image (RepoDigests); check the push." >&2
        exit 1
    fi
fi

# --- 2. Move the Helm-owned reference ---------------------------------------
# --reuse-values keeps every other setting; only this service's image moves,
# so Kubernetes rolls exactly one workload. buildTag and runtimeRelease are
# deliberately NOT touched here: changing either would roll proof services.
echo "[deploy:$logical_name] Setting Helm images.$helm_key=$image ..."
helm upgrade "$HELM_RELEASE_NAME" "$HELM_CHART" --namespace "$NAMESPACE" \
    --reuse-values --set-string "images.$helm_key=$image" >/dev/null
echo "[deploy:$logical_name] Helm release updated."

# --- 3. Honest convergence ---------------------------------------------------
# DaemonSet agents re-verify wiring per session pod before serving, so the
# wait budget scales with the live session size. "Still progressing" extends
# the wait with a progress report; only a STALLED rollout fails.
if [[ "$resource" == daemonset/* ]]; then
    session_pods="$(kubectl get pods -n "$NAMESPACE" -l nodalarc.io/session=true --no-headers 2>/dev/null | wc -l)"
    budget=$(( 120 + 3 * session_pods ))
else
    budget=180
fi
echo "[deploy:$logical_name] Waiting for rollout of $resource (budget ${budget}s, extends while progressing)..."
deadline=$(( SECONDS + budget ))
last_progress=""
stall_since=$SECONDS
while true; do
    if kubectl rollout status "$resource" -n "$NAMESPACE" --timeout=15s >/dev/null 2>&1; then
        echo "[deploy:$logical_name] Rollout complete."
        break
    fi
    progress="$(kubectl get "$resource" -n "$NAMESPACE" -o jsonpath='{.status}' 2>/dev/null | head -c 300)"
    if [ "$progress" != "$last_progress" ]; then
        last_progress="$progress"
        stall_since=$SECONDS
        echo "[deploy:$logical_name]   ...progressing"
    fi
    if [ $SECONDS -ge $deadline ] && [ $(( SECONDS - stall_since )) -ge 120 ]; then
        echo "[deploy:$logical_name] ERROR: rollout stalled (no status change for 120s past budget)." >&2
        kubectl get "$resource" -n "$NAMESPACE" >&2
        echo "[deploy:$logical_name] Next: kubectl describe $resource -n $NAMESPACE; make status." >&2
        exit 1
    fi
done

# --- 4. Prove it -------------------------------------------------------------
deployed_ref="$(kubectl get "$resource" -n "$NAMESPACE" -o jsonpath='{.spec.template.spec.containers[0].image}')"
if [ "$deployed_ref" != "$image" ]; then
    echo "[deploy:$logical_name] ERROR: spec drift — deployed=$deployed_ref expected=$image" >&2
    exit 1
fi
app_label="$(kubectl get "$resource" -n "$NAMESPACE" -o jsonpath='{.spec.selector.matchLabels.app}')"
running_ids="$(kubectl get pods -n "$NAMESPACE" -l "app=$app_label" \
    -o jsonpath='{range .items[*]}{.status.containerStatuses[0].imageID}{"\n"}{end}' | sort -u | grep -v '^$' || true)"
if [ -n "$pushed_digest" ]; then
    if echo "$running_ids" | grep -qF "$pushed_digest"; then
        echo "[deploy:$logical_name] VERIFIED: running digest matches pushed ${pushed_digest:0:19}..."
    else
        echo "[deploy:$logical_name] ERROR: running pods do not match the pushed digest." >&2
        echo "  pushed:  $pushed_digest" >&2
        echo "  running: $running_ids" >&2
        echo "[deploy:$logical_name] Next: make status (drift table); check imagePullPolicy and registry." >&2
        exit 1
    fi
elif [ "$MODE_RESOLVED" = "single-node" ]; then
    echo "[deploy:$logical_name] Spec verified (tag $image); digest proof unavailable with single-node ctr import."
else
    echo "[deploy:$logical_name] ERROR: no pushed digest recorded in multi-node mode; refusing to claim verification." >&2
    exit 1
fi

echo "[deploy:$logical_name] Done. Next: make status"
