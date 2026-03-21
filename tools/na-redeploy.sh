#!/bin/bash
# Tear down and redeploy a Nodal Arc session.
# Usage: na-redeploy.sh [--session <path>] [--dwell <float>]
# If no --session, finds the most recent session-state.json and re-uses its config.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
UV="${UV:-/home/chance/.local/bin/uv}"

SESSION=""
DWELL=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --session) SESSION="$2"; shift 2 ;;
        --dwell)   DWELL="$2"; shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

# Auto-detect session from most recent session-state.json
if [ -z "$SESSION" ]; then
    STATE_FILE=$(find /var/nodalarc/sessions -name session-state.json -printf '%T@ %p\n' 2>/dev/null \
        | sort -rn | head -1 | awk '{print $2}')
    if [ -z "$STATE_FILE" ]; then
        echo "ERROR: No --session provided and no session-state.json found under /var/nodalarc/sessions/"
        exit 1
    fi
    SESSION=$(jq -r '.session_config' "$STATE_FILE")
    echo "Auto-detected session config: $SESSION (from $STATE_FILE)"
fi

echo "=== Redeploy: $SESSION ==="
START_TIME=$SECONDS

# Teardown
"$SCRIPT_DIR/na-teardown.sh"
echo "Waiting 2s for port release..."
sleep 2

# Deploy (--skip-teardown since we already tore down)
DWELL_ARG=""
if [ -n "$DWELL" ]; then
    DWELL_ARG="--dwell $DWELL"
fi
sudo KUBECONFIG="$KUBECONFIG" "$UV" run python -m tools.legacy.na_deploy --session "$SESSION" --skip-teardown $DWELL_ARG

ELAPSED=$((SECONDS - START_TIME))
echo ""
echo "=== Redeploy complete in ${ELAPSED}s ==="
