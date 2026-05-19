#!/usr/bin/env bash
# Copyright 2024-2026 .chance (dotchance)
# NodalArc status — shows system state and tells you the next step.
# Called by `make status`. Environment: NAMESPACE, REGISTRY_HOST, DEFAULT_SESSION.
set -euo pipefail

NAMESPACE="${NAMESPACE:-nodalarc}"
DEFAULT_SESSION="${DEFAULT_SESSION:-configs/sessions/demo-36-ospf.yaml}"
REGISTRY_HOST="${REGISTRY_HOST:-}"
TAG="${TAG:-dev}"

mapfile -t BUILD_IMAGES < <(
    TAG="$TAG" REGISTRY_HOST="$REGISTRY_HOST" NA_IMAGES_NO_CLUSTER=1 \
        bash "$(dirname "$0")/na-images.sh" list-build-images | awk -F '\t' '{print $4}'
)
mapfile -t DEPLOY_IMAGES < <(
    TAG="$TAG" REGISTRY_HOST="$REGISTRY_HOST" NA_IMAGES_NO_CLUSTER=1 \
        bash "$(dirname "$0")/na-images.sh" list-nodalarc-runtime-images | awk -F '\t' '{print $4}'
)

registry_manifest_exists() {
    local image="$1"
    local without_host repo tag accept
    [ -n "$REGISTRY_HOST" ] || return 1
    without_host="${image#"$REGISTRY_HOST"/}"
    repo="${without_host%:*}"
    tag="${without_host##*:}"
    accept="application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json"
    curl -sf --max-time 2 -H "Accept: $accept" \
        "http://$REGISTRY_HOST/v2/$repo/manifests/$tag" >/dev/null 2>&1
}

print_problem_pod_diagnostics() {
    local pod="$1"
    local pod_json logs

    echo "    $pod:"

    pod_json="$(kubectl get pod "$pod" -n "$NAMESPACE" -o json 2>/dev/null || true)"
    if [ -n "$pod_json" ]; then
        echo "$pod_json" | python3 -c '
import json
import sys

d = json.load(sys.stdin)
for cs in d.get("status", {}).get("containerStatuses", []) or []:
    name = cs.get("name", "unknown")
    restarts = cs.get("restartCount", 0)
    print(f"      container: {name} (restarts={restarts})")

    state = cs.get("state") or {}
    if "waiting" in state:
        waiting = state["waiting"] or {}
        reason = waiting.get("reason") or "Unknown"
        message = (waiting.get("message") or "").replace("\n", " ").strip()
        print(f"      current: waiting reason={reason}")
        if message:
            print(f"      message: {message[:220]}")
    elif "terminated" in state:
        term = state["terminated"] or {}
        reason = term.get("reason") or "Unknown"
        exit_code = term.get("exitCode", "?")
        signal = term.get("signal")
        finished = term.get("finishedAt") or "unknown"
        suffix = f", signal={signal}" if signal is not None else ""
        print(f"      current: terminated reason={reason}, exitCode={exit_code}{suffix}, finishedAt={finished}")

    last = cs.get("lastState") or {}
    if "terminated" in last:
        term = last["terminated"] or {}
        reason = term.get("reason") or "Unknown"
        exit_code = term.get("exitCode", "?")
        signal = term.get("signal")
        finished = term.get("finishedAt") or "unknown"
        suffix = f", signal={signal}" if signal is not None else ""
        print(f"      last: terminated reason={reason}, exitCode={exit_code}{suffix}, finishedAt={finished}")
        message = (term.get("message") or "").replace("\n", " ").strip()
        if message:
            print(f"      last message: {message[:220]}")
'
    else
        echo "      pod details unavailable"
    fi

    logs="$(kubectl logs -n "$NAMESPACE" "$pod" --all-containers=true --tail=12 --previous 2>&1 || true)"
    if echo "$logs" | grep -Eqi "previous terminated container|not found"; then
        logs="$(kubectl logs -n "$NAMESPACE" "$pod" --all-containers=true --tail=12 2>&1 || true)"
    fi

    if [ -n "$logs" ]; then
        echo "      recent logs:"
        echo "$logs" | tail -n 12 | sed 's/^/        /'
    else
        echo "      recent logs: unavailable"
    fi
}

echo "=== NodalArc Status ==="
echo "Copyright 2024-2026 .chance (dotchance)"
echo "Official source: https://github.com/dotchance/nodalarc"
echo ""

# --- Cluster ---
echo "Cluster:"
if ! kubectl cluster-info >/dev/null 2>&1; then
    echo "  NOT REACHABLE (check KUBECONFIG=${KUBECONFIG:-not set})"
    exit 0
fi
NODE_COUNT=$(kubectl get nodes --no-headers 2>/dev/null | wc -l)
NODE_READY=$(kubectl get nodes --no-headers 2>/dev/null | grep -c " Ready" || true)
if [ "$NODE_COUNT" -eq 1 ]; then
    echo "  Single-node ($NODE_READY/$NODE_COUNT ready)"
else
    echo "  Multi-node ($NODE_READY/$NODE_COUNT nodes ready)"
fi
kubectl get nodes --no-headers 2>/dev/null | awk '{printf "    %s: %s  (%s, %s)\n", $1, $2, $4, $5}'
echo ""

# --- Platform ---
if ! kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
    echo "Platform: NOT INSTALLED"

    MISSING_BUILD=""
    for img in "${BUILD_IMAGES[@]}"; do
        if ! docker image inspect "$img" >/dev/null 2>&1; then
            MISSING_BUILD="$MISSING_BUILD $img"
        fi
    done

    if [ -n "$MISSING_BUILD" ]; then
        echo "  Missing images:$MISSING_BUILD"
        echo "  Run: make all"
    elif [ -n "$REGISTRY_HOST" ]; then
        MISSING_REG=""
        for img in "${DEPLOY_IMAGES[@]}"; do
            if ! registry_manifest_exists "$img"; then
                MISSING_REG="$MISSING_REG $img"
            fi
        done
        if [ -n "$MISSING_REG" ]; then
            echo "  Images built but not in registry."
            echo "  Run: make load && make install"
        else
            echo "  Images built and loaded."
            echo "  Run: make install"
        fi
    else
        echo "  All images built."
        echo "  Run: make install"
    fi
    exit 0
fi

echo "Platform:"
PLATFORM=$(kubectl get pods -n "$NAMESPACE" --no-headers -o wide 2>/dev/null | grep -E "nodalarc-|nodalpath-|ome-" || true)
PLATFORM_HEALTHY=false
if [ -z "$PLATFORM" ]; then
    echo "  NOT RUNNING"
    echo "  Run: make install"
else
    TOTAL=$(echo "$PLATFORM" | wc -l)
    RUNNING=$(echo "$PLATFORM" | grep -c Running || true)
    if [ "$RUNNING" -eq "$TOTAL" ]; then
        echo "  Running ($RUNNING/$TOTAL platform pods)"
        PLATFORM_HEALTHY=true
    else
        echo "  DEGRADED ($RUNNING/$TOTAL platform pods running)"
        IMG_PULL=$(echo "$PLATFORM" | grep -c "ImagePull\|ErrImagePull" || true)
        CRASH=$(echo "$PLATFORM" | grep -c "CrashLoopBackOff\|Error" || true)

        if [ "$IMG_PULL" -gt 0 ]; then
            ome_image=""
            for img in "${DEPLOY_IMAGES[@]}"; do
                case "$img" in
                    */nodalarc/ome:*|nodalarc/ome:*) ome_image="$img" ;;
                esac
            done
            if [ -n "$REGISTRY_HOST" ] && [ -n "$ome_image" ] && registry_manifest_exists "$ome_image"; then
                echo "  $IMG_PULL pod(s) stuck in ImagePullBackOff (images are in registry)."
                echo "  Run: make restart"
            elif [ -n "$ome_image" ] && docker image inspect "$ome_image" >/dev/null 2>&1; then
                echo "  $IMG_PULL pod(s) failing to pull images."
                echo "  Images built locally but not in registry."
                echo "  Run: make load"
            else
                echo "  $IMG_PULL pod(s) failing to pull images."
                echo "  Images not built."
                echo "  Run: make build"
            fi
        elif [ "$CRASH" -gt 0 ]; then
            echo "  $CRASH pod(s) crashing."
        fi

        PROBLEM_PODS=$(echo "$PLATFORM" | awk '$3 != "Running" {print $1}' || true)
        if [ -n "$PROBLEM_PODS" ]; then
            echo "  Problem pod diagnostics:"
            while IFS= read -r pod; do
                [ -n "$pod" ] || continue
                print_problem_pod_diagnostics "$pod"
            done <<< "$PROBLEM_PODS"
        fi
    fi
    echo "$PLATFORM" | awk '{printf "    %-45s %-10s %s\n", $1, $3, $7}'
fi
echo ""

# --- Services ---
echo "Services:"
VF_NODE=$(kubectl get pod -n "$NAMESPACE" -l app=nodalarc-vf -o jsonpath="{.items[0].spec.nodeName}" 2>/dev/null || true)
VF_IP=""
if [ -n "$VF_NODE" ]; then
    VF_IP=$(kubectl get node "$VF_NODE" -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || true)
fi
API_NODE=$(kubectl get pod -n "$NAMESPACE" -l app=nodalarc-vs-api -o jsonpath="{.items[0].spec.nodeName}" 2>/dev/null || true)
API_IP=""
if [ -n "$API_NODE" ]; then
    API_IP=$(kubectl get node "$API_NODE" -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || true)
fi
if [ -n "$VF_IP" ]; then
    echo "  Visualization:  http://$VF_IP:3000  (on $VF_NODE)"
else
    echo "  Visualization:  NOT AVAILABLE"
fi
if [ -n "$API_IP" ]; then
    echo "  VS-API:         http://$API_IP:8080  (on $API_NODE)"
else
    echo "  VS-API:         NOT AVAILABLE"
fi
echo ""

# --- Session ---
echo "Session:"
if [ "$PLATFORM_HEALTHY" = "false" ]; then
    echo "  Platform not healthy — fix platform issues before starting a session"
else
    SESSION=$(kubectl get constellationspec current-session -n "$NAMESPACE" -o json 2>/dev/null || true)
    if [ -z "$SESSION" ]; then
        echo "  No session deployed"
        echo "  Available sessions:"
        for f in configs/sessions/*.yaml; do
            name=$(basename "$f")
            if [ "$f" = "$DEFAULT_SESSION" ]; then
                echo "    $name (default)"
            else
                echo "    $name"
            fi
        done
        echo "  Run: make session"
        echo "  Override: make session DEFAULT_SESSION=configs/sessions/<name>.yaml"
    else
        SESSION_NAME=$(echo "$SESSION" | python3 -c "import json,sys,yaml; d=json.load(sys.stdin); status=d.get('status',{}); name=status.get('sessionId') or yaml.safe_load(d.get('spec',{}).get('sessionYaml','{}')).get('session',{}).get('name','unknown'); print(name)" 2>/dev/null || echo "unknown")
        PHASE=$(echo "$SESSION" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('status',{}).get('phase','Unknown'))" 2>/dev/null || echo "Unknown")
        WIRED=$(echo "$SESSION" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('status',{}).get('wiredPods',0))" 2>/dev/null || echo "0")
        SATS=$(kubectl get pods -n "$NAMESPACE" -l nodalarc.io/role=satellite --no-headers 2>/dev/null | grep -c Running || echo 0)
        GS=$(kubectl get pods -n "$NAMESPACE" -l nodalarc.io/role=ground-station --no-headers 2>/dev/null | grep -c Running || echo 0)
        echo "  Name: $SESSION_NAME"
        echo "  Phase: $PHASE"
        echo "  Satellites: $SATS running"
        echo "  Ground stations: $GS running"
        echo "  Wired: $WIRED nodes"
        NOT_RUNNING=$(kubectl get pods -n "$NAMESPACE" -l nodalarc.io/node-id --no-headers 2>/dev/null | grep -v Running | grep -v Completed || true)
        if [ -n "$NOT_RUNNING" ]; then
            echo "  WARNING: Some session pods not running:"
            echo "$NOT_RUNNING" | awk '{printf "    %s: %s\n", $1, $3}'
        fi
    fi
fi
echo ""

# --- Pod Distribution ---
echo "Pod Distribution:"
SESSION_PODS=$(kubectl get pods -n "$NAMESPACE" -l nodalarc.io/node-id -o wide --no-headers 2>/dev/null || true)
if [ -z "$SESSION_PODS" ]; then
    echo "  No session pods"
else
    echo "$SESSION_PODS" | awk '{nodes[$7]++} END {for (n in nodes) printf "  %s: %d session pods\n", n, nodes[n]}'
    echo ""
    echo "  Satellites:"
    echo "$SESSION_PODS" | grep "nodalarc.io/role=satellite" >/dev/null 2>&1 || true
    kubectl get pods -n "$NAMESPACE" -l nodalarc.io/role=satellite -o wide --no-headers 2>/dev/null | \
        awk '{printf "    %-25s %-10s %s\n", $1, $3, $7}' || true
    echo "  Ground Stations:"
    kubectl get pods -n "$NAMESPACE" -l nodalarc.io/role=ground-station -o wide --no-headers 2>/dev/null | \
        awk '{printf "    %-25s %-10s %s\n", $1, $3, $7}' || true
fi
echo ""

# --- Links ---
echo "Links:"
if [ -z "${API_IP:-}" ]; then
    echo "  VS-API not running"
else
    TOKEN=$(curl -s "http://$API_IP:8080/api/v1/auth/token" 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin).get('token',''))" 2>/dev/null || true)
    if [ -n "$TOKEN" ]; then
        curl -s -H "Authorization: Bearer $TOKEN" "http://$API_IP:8080/api/v1/state" 2>/dev/null | \
            python3 -c "
import json,sys
s=json.load(sys.stdin)
intra=sum(1 for l in s['links'] if l.get('link_type')=='intra_plane_isl')
cross=sum(1 for l in s['links'] if l.get('link_type')=='cross_plane_isl')
gnd=sum(1 for l in s['links'] if l.get('link_type')=='ground')
print(f'  Intra-plane ISL: {intra}')
print(f'  Cross-plane ISL: {cross}')
print(f'  Ground links: {gnd}')
print(f'  Total active: {len(s[\"links\"])}')
" 2>/dev/null || echo "  Unable to query VS-API at http://$API_IP:8080"
    else
        echo "  VS-API not reachable at http://$API_IP:8080"
    fi
fi
