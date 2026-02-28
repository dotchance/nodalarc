#!/bin/bash
# Show Nodal Arc deployment status.
set -euo pipefail

echo "=== Nodal Arc Status ==="
echo ""
echo "--- Helm Releases ---"
helm list -n nodalarc 2>/dev/null || echo "(no releases)"
echo ""
echo "--- Pods ---"
kubectl get pods -n nodalarc -o wide 2>/dev/null || echo "(no pods)"
echo ""
echo "--- Pod Resource Usage ---"
kubectl top pods -n nodalarc 2>/dev/null || echo "(metrics unavailable)"
