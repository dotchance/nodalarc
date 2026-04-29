#!/usr/bin/env bash
# Copyright 2024-2026 .chance (dotchance)
# NodalArc status — shows system state and tells you the next step.
# Called by `make status`. Environment: NAMESPACE, REGISTRY_HOST, DEFAULT_SESSION.
set -euo pipefail

NAMESPACE="${NAMESPACE:-nodalarc}"
DEFAULT_SESSION="${DEFAULT_SESSION:-configs/sessions/demo-36-ospf.yaml}"
REGISTRY_HOST="${REGISTRY_HOST:-}"

# Images that must exist locally for build
BUILD_IMAGES="nodalarc/base nodalarc/frr nodalarc/probe nodalarc/nodalpath-fwd nodalarc/ome nodalarc/scheduler nodalarc/node-agent nodalarc/vs-api nodalarc/operator nodalarc/vf nodalarc/nodalpath"
# Images that K8s pods actually pull — subset that must be in the registry
DEPLOY_IMAGES="nodalarc/frr nodalarc/ome nodalarc/scheduler nodalarc/node-agent nodalarc/vs-api nodalarc/operator nodalarc/vf nodalarc/nodalpath"

echo "=== NodalArc Status ==="
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

    BUILT_IMAGES=$(docker images --format '{{.Repository}}' 2>/dev/null | sed "s|^${REGISTRY_HOST}/||" | sort -u)
    MISSING_BUILD=""
    for img in $BUILD_IMAGES; do
        if ! echo "$BUILT_IMAGES" | grep -q "^${img}$"; then
            MISSING_BUILD="$MISSING_BUILD $img"
        fi
    done

    if [ -n "$MISSING_BUILD" ]; then
        echo "  Missing images:$MISSING_BUILD"
        if [ -n "$REGISTRY_HOST" ]; then
            echo "  Run: make build && make load && make install"
        else
            echo "  Run: make build && make install"
        fi
    elif [ -n "$REGISTRY_HOST" ]; then
        MISSING_REG=""
        for img in $DEPLOY_IMAGES; do
            if ! curl -sf --max-time 2 "http://$REGISTRY_HOST/v2/$img/tags/list" >/dev/null 2>&1; then
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
            if [ -n "$REGISTRY_HOST" ] && curl -sf --max-time 2 "http://$REGISTRY_HOST/v2/nodalarc/ome/tags/list" >/dev/null 2>&1; then
                echo "  $IMG_PULL pod(s) stuck in ImagePullBackOff (images are in registry)."
                echo "  Run: make restart"
            elif docker images --format '{{.Repository}}' 2>/dev/null | grep -q 'nodalarc/ome'; then
                echo "  $IMG_PULL pod(s) failing to pull images."
                echo "  Images built locally but not in registry."
                echo "  Run: make load"
            else
                echo "  $IMG_PULL pod(s) failing to pull images."
                echo "  Images not built."
                echo "  Run: make build"
            fi
        elif [ "$CRASH" -gt 0 ]; then
            CRASH_POD=$(echo "$PLATFORM" | grep -E "CrashLoopBackOff|Error" | awk '{print $1}' | head -1)
            echo "  $CRASH pod(s) crashing. Inspect with:"
            echo "    kubectl logs -n $NAMESPACE $CRASH_POD"
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
        SESSION_NAME=$(echo "$SESSION" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('status',{}).get('sessionId','unknown'))" 2>/dev/null || echo "unknown")
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
