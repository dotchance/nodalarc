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

# The service list comes from the image inventory: every required workload
# the tree builds (pinned upstream images have no tree tag to drift from).
# The gate never passes on an inventory it could not read: a failed or empty
# read is drift, because nothing was checked.
if ! resources="$(bash "$ROOT_DIR/scripts/na-images.sh" list-platform-resources)" || [ -z "$resources" ]; then
    echo "Platform drift cannot be checked: the image inventory did not answer." >&2
    exit 1
fi
drifted=0
rows=""
checked=0
while IFS=$'\t' read -r logical resource source; do
    [ -n "$logical" ] || continue
    [ "$source" = "built" ] || continue
    checked=$((checked + 1))
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
done <<< "$resources"
if [ "$checked" -eq 0 ]; then
    echo "Platform drift cannot be checked: the image inventory names no built workload." >&2
    exit 1
fi

# Session pods change only on make session; the FRR row is shown without
# gating on it. Session pods name the FRR image the way make session gave it:
# the tree's tag and the registry's digest (the tag alone in single-node mode).
short_ref() {
    local ref="${1##*/}" digest=""
    ref="${ref#*:}"
    case "$ref" in
        *@sha256:*) digest="${ref##*@sha256:}"; ref="${ref%%@*}@${digest:0:12}" ;;
    esac
    printf '%s' "$ref"
}
frr_tag_ref="$(bash "$ROOT_DIR/scripts/na-images.sh" image-for frr 2>/dev/null || echo "?")"
frr_repo="${frr_tag_ref%:*}"
frr_tree="$(bash "$ROOT_DIR/scripts/na-images.sh" session-image-for frr 2>/dev/null || true)"
frr_running="$(kubectl get pods -n "$NAMESPACE" -l nodalarc.io/session=true \
    -o jsonpath='{.items[*].spec.containers[*].image}' 2>/dev/null \
    | tr ' ' '\n' | awk -v repo="$frr_repo" 'index($0, repo ":") == 1 || index($0, repo "@") == 1' \
    | sort -u || true)"
if [ -z "$frr_running" ]; then
    frr_shown="no FRR session pods"
    frr_marker="info"
else
    frr_shown="$(printf '%s\n' "$frr_running" | while IFS= read -r ref; do short_ref "$ref"; echo; done | paste -sd ' ')"
    if [ -z "$frr_tree" ]; then
        frr_marker="UNKNOWN (the registry did not name the tree's FRR image)"
    elif [ "$frr_running" = "$frr_tree" ]; then
        frr_marker="ok"
    else
        frr_marker="STALE (sessions redeploy on make session)"
    fi
fi
rows+="frr (session pods)|$(short_ref "${frr_tree:-?}")|$frr_shown|$frr_marker"$'\n'

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
