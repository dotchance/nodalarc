#!/bin/bash
# Copyright 2024-2026 .chance (dotchance)
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

echo "--- Pod Counts ---"
SAT_COUNT=$(kubectl get pods -n nodalarc -l nodalarc.io/role=satellite --no-headers 2>/dev/null | wc -l)
GS_COUNT=$(kubectl get pods -n nodalarc -l nodalarc.io/role=ground_station --no-headers 2>/dev/null | wc -l)
echo "Satellites: $SAT_COUNT"
echo "Ground stations: $GS_COUNT"
echo ""

echo "--- Local Processes ---"
pgrep -af "orchestrator.main" 2>/dev/null || echo "(no orchestrator running)"
pgrep -af "convergence_stub" 2>/dev/null || echo "(no MI stub running)"
echo ""

echo "--- Session State Files ---"
find /var/nodalarc/sessions -name session-state.json 2>/dev/null || echo "(none)"
