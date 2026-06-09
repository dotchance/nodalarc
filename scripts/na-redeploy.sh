#!/usr/bin/env bash
# Compatibility wrapper for redeploying a session through the official lifecycle paths.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
NAMESPACE="${NAMESPACE:-nodalarc}"
SESSION=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --session)
            SESSION="$2"
            shift 2
            ;;
        *)
            echo "Unknown arg: $1" >&2
            exit 2
            ;;
    esac
done

if [ -z "$SESSION" ]; then
    echo "ERROR: --session <path> is required" >&2
    echo "Usage: na-redeploy.sh --session catalog/nodalarc/sessions/earth-leo-walker.yaml" >&2
    exit 2
fi

if [ ! -f "$SESSION" ]; then
    echo "ERROR: Session file not found: $SESSION" >&2
    exit 1
fi

echo "=== Redeploy: $SESSION ==="

if kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
    ACTION=reinstall NAMESPACE="$NAMESPACE" bash "$SCRIPT_DIR/na-install-platform.sh"
else
    ACTION=install NAMESPACE="$NAMESPACE" bash "$SCRIPT_DIR/na-install-platform.sh"
fi

DEFAULT_SESSION="$SESSION" NAMESPACE="$NAMESPACE" bash "$SCRIPT_DIR/na-session.sh"
