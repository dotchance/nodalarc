#!/bin/bash
# Comprehensive Nodal Arc teardown — no arguments needed.
# Kills ALL nodal-arc processes, uninstalls ALL Helm releases, cleans up sockets.
# Idempotent: exits 0 even if nothing was running.
set -u

export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"

echo "=== Nodal Arc Teardown ==="

# --- Step 1-3: SIGTERM all nodal-arc processes ---
PYTHON_PATTERNS=(
    "ome.main"
    "orchestrator.main"
    "vs_api.main"
    "measurement.mi_main"
    "nodalpath"
    "tools.deploy_daemon"
    "tools.na_deploy"
)

echo "Sending SIGTERM to Python backends..."
for pat in "${PYTHON_PATTERNS[@]}"; do
    pids=$(pgrep -f "$pat" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo "  SIGTERM $pat (PIDs: $pids)"
        sudo kill $pids 2>/dev/null || true
    fi
done

echo "Sending SIGTERM to Vite dev server..."
pkill -f "node_modules/.bin/vite" 2>/dev/null || true
sudo pkill -f "node_modules/.bin/vite" 2>/dev/null || true

echo "Sending SIGTERM to stale uv run wrappers..."
# Only kill uv wrappers for known nodal-arc modules, NOT na_deploy or integration test
for upat in "uv run python -m ome" "uv run python -m orchestrator" "uv run python -m vs_api" "uv run python -m measurement"; do
    pids=$(pgrep -f "$upat" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo "  SIGTERM '$upat' (PIDs: $pids)"
        sudo kill $pids 2>/dev/null || true
    fi
done

# --- Step 4: Grace period for clean shutdown ---
echo "Waiting 3s for graceful shutdown..."
sleep 3

# --- Step 5-6: SIGKILL survivors ---
echo "SIGKILL any survivors..."
for pat in "${PYTHON_PATTERNS[@]}"; do
    pids=$(pgrep -f "$pat" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo "  SIGKILL $pat (PIDs: $pids)"
        sudo kill -9 $pids 2>/dev/null || true
    fi
done
pkill -9 -f "node_modules/.bin/vite" 2>/dev/null || true
sudo pkill -9 -f "node_modules/.bin/vite" 2>/dev/null || true
for upat in "uv run python -m ome" "uv run python -m orchestrator" "uv run python -m vs_api" "uv run python -m measurement"; do
    pids=$(pgrep -f "$upat" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo "  SIGKILL '$upat' (PIDs: $pids)"
        sudo kill -9 $pids 2>/dev/null || true
    fi
done
sleep 1

# --- Step 7: Uninstall ALL Helm releases in nodalarc namespace ---
echo "Uninstalling Helm releases in nodalarc namespace..."
releases=$(sudo KUBECONFIG="$KUBECONFIG" helm list -n nodalarc -q --all 2>/dev/null || true)
if [ -n "$releases" ]; then
    for rel in $releases; do
        echo "  Uninstalling: $rel"
        sudo KUBECONFIG="$KUBECONFIG" helm uninstall "$rel" -n nodalarc 2>/dev/null || true
    done
else
    echo "  (no releases found)"
fi

# Clean up any stale K8s resources (ServiceAccounts, Roles, ConfigMaps, etc.)
sudo KUBECONFIG="$KUBECONFIG" kubectl delete all --all -n nodalarc 2>/dev/null || true
sudo KUBECONFIG="$KUBECONFIG" kubectl delete serviceaccount --all -n nodalarc 2>/dev/null || true
sudo KUBECONFIG="$KUBECONFIG" kubectl delete role --all -n nodalarc 2>/dev/null || true
sudo KUBECONFIG="$KUBECONFIG" kubectl delete rolebinding --all -n nodalarc 2>/dev/null || true
sudo KUBECONFIG="$KUBECONFIG" kubectl delete configmap --all -n nodalarc 2>/dev/null || true
sudo KUBECONFIG="$KUBECONFIG" kubectl delete endpoints --all -n nodalarc 2>/dev/null || true

# --- Step 8: Wait for pods to terminate ---
echo "Waiting for pods to terminate (up to 60s)..."
elapsed=0
while [ $elapsed -lt 60 ]; do
    pod_output=$(sudo KUBECONFIG="$KUBECONFIG" kubectl get pods -n nodalarc --no-headers 2>/dev/null || true)
    if [ -z "$pod_output" ]; then pod_count=0; else pod_count=$(echo "$pod_output" | wc -l); fi
    if [ "$pod_count" -eq 0 ]; then
        break
    fi
    echo "  $pod_count pods remaining..."
    sleep 2
    elapsed=$((elapsed + 2))
done

# --- Step 9: Clean up sockets ---
rm -f /tmp/nodal-deploy.sock

# --- Step 10: Final verification ---
echo ""
echo "=== Verification ==="
remaining_procs=""
for pat in "${PYTHON_PATTERNS[@]}"; do
    pids=$(pgrep -f "$pat" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        remaining_procs="$remaining_procs  $pat (PIDs: $pids)\n"
    fi
done
vite_pids=$(pgrep -f "node_modules/.bin/vite" 2>/dev/null || true)
if [ -n "$vite_pids" ]; then
    remaining_procs="$remaining_procs  vite (PIDs: $vite_pids)\n"
fi

if [ -n "$remaining_procs" ]; then
    echo "WARNING: Stale processes remain:"
    echo -e "$remaining_procs"
else
    echo "Processes: clean"
fi

pod_output=$(sudo KUBECONFIG="$KUBECONFIG" kubectl get pods -n nodalarc --no-headers 2>/dev/null || true)
if [ -z "$pod_output" ]; then pod_count=0; else pod_count=$(echo "$pod_output" | wc -l); fi
if [ "$pod_count" -gt 0 ]; then
    echo "WARNING: $pod_count pods still in nodalarc namespace"
    sudo KUBECONFIG="$KUBECONFIG" kubectl get pods -n nodalarc 2>/dev/null || true
else
    echo "Pods: clean"
fi

echo ""
echo "=== Teardown complete ==="
