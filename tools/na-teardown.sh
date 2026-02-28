#!/bin/bash
# Tear down a Nodal Arc deployment.
# Usage: na-teardown.sh [session-id]
set -euo pipefail

SESSION_ID="${1:-}"

if [ -z "$SESSION_ID" ]; then
    echo "Usage: na-teardown.sh <session-id>"
    echo ""
    echo "Active releases:"
    helm list -n nodalarc 2>/dev/null || echo "  (none)"
    exit 1
fi

echo "Tearing down session: $SESSION_ID"

helm uninstall "$SESSION_ID" -n nodalarc 2>/dev/null || true

# Wait for pods to terminate
echo "Waiting for pods to terminate..."
kubectl wait --for=delete pod -l nodalarc.io/node-id -n nodalarc --timeout=60s 2>/dev/null || true

echo "Teardown complete"
