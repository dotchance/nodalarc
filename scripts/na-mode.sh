#!/usr/bin/env bash
# Canonical lifecycle mode resolver for Make and lifecycle scripts.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

MODE="${MODE:-auto}"
REGISTRY_HOST="${REGISTRY_HOST:-}"
REGISTRY_PREFIX="${REGISTRY_PREFIX:-}"
KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
export KUBECONFIG

usage() {
    cat <<'EOF'
usage: na-mode.sh [resolve]

Prints one tab-separated record:
mode<TAB>registry_host<TAB>registry_prefix<TAB>node_count<TAB>mirror_third_party
EOF
}

node_count() {
    if ! command -v kubectl >/dev/null 2>&1; then
        printf '0\n'
        return
    fi
    kubectl get nodes --no-headers 2>/dev/null | awk 'END {print NR + 0}'
}

case "${1:-resolve}" in
    resolve) ;;
    -h|--help|help)
        usage
        exit 0
        ;;
    *)
        echo "na-mode: unknown command: $1" >&2
        usage >&2
        exit 2
        ;;
esac

case "$MODE" in
    auto|single-node|multi-node) ;;
    *)
        echo "na-mode: MODE must be auto, single-node, or multi-node; got '$MODE'" >&2
        exit 2
        ;;
esac

if [ -n "$REGISTRY_PREFIX" ] && [ -z "$REGISTRY_HOST" ]; then
    echo "na-mode: REGISTRY_PREFIX is set without REGISTRY_HOST; set REGISTRY_HOST instead" >&2
    exit 2
fi

if [ -z "$REGISTRY_HOST" ] && [ "$MODE" = "auto" ]; then
    detected="$(bash "$ROOT_DIR/scripts/detect-registry.sh" 2>/dev/null || true)"
    if [ -n "$detected" ]; then
        REGISTRY_HOST="$detected"
        echo "na-mode: inferred REGISTRY_HOST=$REGISTRY_HOST from K3s registries.yaml" >&2
    fi
fi

NODE_COUNT="$(node_count)"

case "$MODE" in
    single-node)
        RESOLVED_MODE="single-node"
        RESOLVED_HOST=""
        RESOLVED_PREFIX=""
        ;;
    multi-node)
        if [ -z "$REGISTRY_HOST" ]; then
            echo "na-mode: MODE=multi-node requires REGISTRY_HOST" >&2
            exit 2
        fi
        RESOLVED_MODE="multi-node"
        RESOLVED_HOST="$REGISTRY_HOST"
        RESOLVED_PREFIX="${REGISTRY_HOST}/"
        ;;
    auto)
        if [ -n "$REGISTRY_HOST" ]; then
            RESOLVED_MODE="multi-node"
            RESOLVED_HOST="$REGISTRY_HOST"
            RESOLVED_PREFIX="${REGISTRY_HOST}/"
        elif [ "$NODE_COUNT" -gt 1 ]; then
            echo "na-mode: multi-node cluster detected but REGISTRY_HOST is empty" >&2
            echo "na-mode: set REGISTRY_HOST or explicitly use MODE=single-node for local-only commands" >&2
            exit 2
        else
            RESOLVED_MODE="single-node"
            RESOLVED_HOST=""
            RESOLVED_PREFIX=""
        fi
        ;;
esac

printf '%s\t%s\t%s\t%s\t%s\n' \
    "$RESOLVED_MODE" \
    "$RESOLVED_HOST" \
    "$RESOLVED_PREFIX" \
    "$NODE_COUNT" \
    "${MIRROR_THIRD_PARTY:-0}"
