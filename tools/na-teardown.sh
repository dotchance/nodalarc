#!/bin/bash
# Tear down a Nodal Arc deployment.
# Usage: na-teardown.sh [session-id] [data-dir]
set -euo pipefail

SESSION_ID="${1:-}"
DATA_DIR="${2:-}"

if [ -z "$SESSION_ID" ]; then
    echo "Usage: na-teardown.sh <session-id> [data-dir]"
    echo ""
    echo "Active releases:"
    helm list -n nodalarc 2>/dev/null || echo "  (none)"
    exit 1
fi

echo "Tearing down session: $SESSION_ID"

# Kill local processes if session-state.json exists
STATE_FILE=""
if [ -n "$DATA_DIR" ] && [ -f "$DATA_DIR/session-state.json" ]; then
    STATE_FILE="$DATA_DIR/session-state.json"
elif [ -f "/tmp/nodalarc/sessions/$SESSION_ID/session-state.json" ]; then
    STATE_FILE="/tmp/nodalarc/sessions/$SESSION_ID/session-state.json"
fi

if [ -n "$STATE_FILE" ]; then
    echo "Found session state: $STATE_FILE"
    MI_PID=$(jq -r '.mi_pid // empty' "$STATE_FILE" 2>/dev/null || true)
    TO_PID=$(jq -r '.orchestrator_pid // empty' "$STATE_FILE" 2>/dev/null || true)
    if [ -n "$MI_PID" ]; then
        echo "Killing MI stub (PID $MI_PID)..."
        kill "$MI_PID" 2>/dev/null || true
    fi
    if [ -n "$TO_PID" ]; then
        echo "Killing orchestrator (PID $TO_PID)..."
        kill "$TO_PID" 2>/dev/null || true
    fi
fi

# Uninstall Helm release
helm uninstall "$SESSION_ID" -n nodalarc 2>/dev/null || true

# Wait for pods to terminate
echo "Waiting for pods to terminate..."
kubectl wait --for=delete pod -l nodalarc.io/node-id -n nodalarc --timeout=60s 2>/dev/null || true

echo "Teardown complete"
