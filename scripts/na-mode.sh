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
usage: na-mode.sh [resolve] [--no-cluster]

Prints one key=value record, one key per line:
mode, registry_host, registry_prefix, node_count, mirror_third_party

--no-cluster skips registry discovery and the node count (node_count=0);
it is for commands that must not touch the cluster.
EOF
}

node_count() {
    local nodes
    if ! command -v kubectl >/dev/null 2>&1; then
        printf '0\n'
        return
    fi
    if ! nodes="$(kubectl get nodes --no-headers 2>/dev/null)"; then
        printf '0\n'
        return
    fi
    printf '%s\n' "$nodes" | awk 'NF {count++} END {print count + 0}'
}

NO_CLUSTER=0
for arg in "$@"; do
    case "$arg" in
        resolve) ;;
        --no-cluster) NO_CLUSTER=1 ;;
        -h|--help|help)
            usage
            exit 0
            ;;
        *)
            echo "na-mode: unknown command: $arg" >&2
            usage >&2
            exit 2
            ;;
    esac
done

case "$MODE" in
    auto|single-node|multi-node) ;;
    *)
        echo "na-mode: MODE must be auto, single-node, or multi-node; got '$MODE'" >&2
        exit 2
        ;;
esac

if [ -n "$REGISTRY_PREFIX" ]; then
    echo "na-mode: REGISTRY_PREFIX is not a setting; the lifecycle scripts derive the prefix from REGISTRY_HOST" >&2
    exit 2
fi

if [ "$NO_CLUSTER" -eq 1 ]; then
    NODE_COUNT=0
else
    if [ -z "$REGISTRY_HOST" ] && [ "$MODE" = "auto" ]; then
        detected="$(bash "$ROOT_DIR/scripts/detect-registry.sh" 2>/dev/null || true)"
        if [ -n "$detected" ]; then
            REGISTRY_HOST="$detected"
            echo "na-mode: inferred REGISTRY_HOST=$REGISTRY_HOST from K3s registries.yaml" >&2
        fi
    fi
    NODE_COUNT="$(node_count)"
fi

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

printf 'mode=%s\nregistry_host=%s\nregistry_prefix=%s\nnode_count=%s\nmirror_third_party=%s\n' \
    "$RESOLVED_MODE" \
    "$RESOLVED_HOST" \
    "$RESOLVED_PREFIX" \
    "$NODE_COUNT" \
    "${MIRROR_THIRD_PARTY:-0}"
