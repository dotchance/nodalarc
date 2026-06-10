#!/usr/bin/env bash
# Platform drift truth table: for every NodalArc service, what the current
# TREE would build, what Helm DEPLOYED, and what is actually RUNNING.
#
# The cluster must never look like it is running your code when it is not.
# This is the single answer to "am I testing what I just wrote?" — consumed
# by `make status` (table) and by `make session` (--check gate refuses to
# deploy a session onto a drifted platform).
#
# Usage:
#   na-drift.sh            print the table; exit 0 always
#   na-drift.sh --check    quiet; exit 1 if any platform service drifts

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-nodalarc}"
# Self-derive the content-addressed tag when invoked outside Make.
export TAG="${TAG:-$(bash "$ROOT_DIR/scripts/na-tag.sh")}"
CHECK_MODE="${1:-}"

# logical-name : k8s resource (kept in step with the Makefile deploy targets)
SERVICES=(
    "ome:deployment/ome"
    "scheduler:deployment/nodalarc-scheduler"
    "node-agent:daemonset/nodalarc-node-agent"
    "vs-api:deployment/nodalarc-vs-api"
    "operator:deployment/nodalarc-operator"
    "vf:deployment/nodalarc-vf"
)

drifted=0
rows=""
for entry in "${SERVICES[@]}"; do
    logical="${entry%%:*}"
    resource="${entry#*:}"
    tree_ref="$(bash "$ROOT_DIR/scripts/na-images.sh" image-for "$logical" 2>/dev/null || echo "?")"
    deployed_ref="$(kubectl get "$resource" -n "$NAMESPACE" \
        -o jsonpath='{.spec.template.spec.containers[?(@.name!="wait-nats-streams")].image}' 2>/dev/null \
        | tr ' ' '\n' | grep -F "${tree_ref%%:*}" | head -1 || true)"
    if [ -z "$deployed_ref" ]; then
        deployed_ref="$(kubectl get "$resource" -n "$NAMESPACE" \
            -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || echo "absent")"
    fi
    marker="ok"
    if [ "$deployed_ref" = "absent" ]; then
        marker="ABSENT"
        drifted=1
    elif [ "$deployed_ref" != "$tree_ref" ]; then
        marker="DRIFT"
        drifted=1
    fi
    rows+="$logical|${tree_ref##*:}|${deployed_ref##*:}|$marker"$'\n'
done

# Session pods (FRR et al) change rarely by design; show the deployed FRR
# tag for visibility without gating on it.
frr_tree="$(bash "$ROOT_DIR/scripts/na-images.sh" image-for frr 2>/dev/null || echo "?")"
frr_running="$(kubectl get pods -n "$NAMESPACE" -l nodalarc.io/session=true \
    -o jsonpath='{.items[0].spec.containers[0].image}' 2>/dev/null || echo "no session")"
frr_marker="info"
[ "$frr_running" != "no session" ] && [ "$frr_running" != "$frr_tree" ] && frr_marker="STALE (sessions redeploy on make session)"
rows+="frr (session pods)|${frr_tree##*:}|${frr_running##*:}|$frr_marker"$'\n'

if [ "$CHECK_MODE" = "--check" ]; then
    if [ "$drifted" -ne 0 ]; then
        echo "Platform drift detected — the cluster is not running this tree's images:" >&2
        printf '%s' "$rows" | column -t -s '|' >&2
        exit 1
    fi
    exit 0
fi

echo ""
echo "Image drift (tree vs deployed):"
{
    echo "SERVICE|TREE TAG|DEPLOYED TAG|STATE"
    printf '%s' "$rows"
} | column -t -s '|'
if [ "$drifted" -ne 0 ]; then
    echo ""
    echo "DRIFT present. Next: make deploy-<service> (one service) or make build && make load && make upgrade (all)."
fi
