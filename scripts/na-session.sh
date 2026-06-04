#!/usr/bin/env bash
# Start or replace the current NodalArc session.

set -euo pipefail

NAMESPACE="${NAMESPACE:-nodalarc}"
DEFAULT_SESSION="${DEFAULT_SESSION:-configs/sessions/earth-leo-simple.yaml}"
KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
export KUBECONFIG

if [ ! -f "$DEFAULT_SESSION" ]; then
    echo "[session] ERROR: session file does not exist: $DEFAULT_SESSION" >&2
    exit 1
fi

echo "[session] Computing expected pod count..."
if ! expected_pods="$(PYTHONPATH=lib uv run python -c 'import importlib.util, sys; spec = importlib.util.spec_from_file_location("session_deployer", "services/nodalarc_operator/session_deployer.py"); mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); print(mod.compute_expected_pod_count({"sessionYaml": open(sys.argv[1], encoding="utf-8").read()}))' "$DEFAULT_SESSION")"; then
    echo "[session] ERROR: failed to compute expected pod count for $DEFAULT_SESSION" >&2
    exit 1
fi
if ! [[ "$expected_pods" =~ ^[0-9]+$ ]]; then
    echo "[session] ERROR: expected pod count was not numeric: $expected_pods" >&2
    exit 1
fi
echo "[session] Expected session pods: $expected_pods"

echo "[session] Computing placement policy..."
if ! placement_policy="$(PYTHONPATH=lib uv run python -c 'import sys; from nodalarc.resolve_session import load_session_resolution_from_file; print(load_session_resolution_from_file(sys.argv[1], origin="na-session").runtime_session.placement.policy)' "$DEFAULT_SESSION")"; then
    echo "[session] ERROR: failed to compute placement policy for $DEFAULT_SESSION" >&2
    exit 1
fi
if [ -z "$placement_policy" ]; then
    echo "[session] ERROR: placement policy was empty for $DEFAULT_SESSION" >&2
    exit 1
fi
echo "[session] Placement policy: $placement_policy"

wait_platform_ready() {
    local timeout="${1:-120}"
    local elapsed=0 total avail ds_desired ds_ready not_running

    echo "[session] Waiting for platform rollout to settle (timeout ${timeout}s)..."
    while [ "$elapsed" -lt "$timeout" ]; do
        total="$(kubectl get deployments -n "$NAMESPACE" --no-headers 2>/dev/null | wc -l | tr -d ' ')"
        avail="$(kubectl get deployments -n "$NAMESPACE" --no-headers 2>/dev/null | awk '{if ($4+0 >= 1) c++} END {print c+0}')"
        ds_desired="$(kubectl get ds nodalarc-node-agent -n "$NAMESPACE" -o jsonpath='{.status.desiredNumberScheduled}' 2>/dev/null || echo 0)"
        ds_ready="$(kubectl get ds nodalarc-node-agent -n "$NAMESPACE" -o jsonpath='{.status.numberReady}' 2>/dev/null || echo 0)"
        not_running="$(
            kubectl get pods -n "$NAMESPACE" --no-headers 2>/dev/null \
                | grep -E "nodalarc-|nodalpath-|ome-" \
                | grep -v Running \
                | grep -v Completed || true
        )"

        if [ "$total" -gt 0 ] \
            && [ "$avail" -eq "$total" ] \
            && [ "$ds_ready" -eq "$ds_desired" ] \
            && [ "$ds_desired" -gt 0 ] \
            && [ -z "$not_running" ]; then
            echo ""
            echo "[session] Platform ready: $total deployments available, $ds_ready/$ds_desired Node Agent pods running."
            return 0
        fi

        sleep 2
        elapsed=$((elapsed + 2))
        printf '\r[session]   Platform: deployments %s/%s, Node Agents %s/%s ready (%ss/%ss)' \
            "$avail" "$total" "$ds_ready" "$ds_desired" "$elapsed" "$timeout"
    done

    echo ""
    echo "[session] ERROR: platform rollout did not settle after ${timeout}s" >&2
    if [ -n "${not_running:-}" ]; then
        echo "$not_running" >&2
    fi
    exit 1
}

wait_vs_api_session_state() {
    local expected_nodes="$1"
    local timeout="${2:-120}"
    local elapsed=0 api_node api_ip token_json token state_json parsed
    local node_count stale session_status link_count last_observed

    echo "[session] Waiting for VS-API state to match the active session (timeout ${timeout}s)..."
    while [ "$elapsed" -lt "$timeout" ]; do
        api_node="$(kubectl get pod -n "$NAMESPACE" -l app=nodalarc-vs-api -o jsonpath='{.items[0].spec.nodeName}' 2>/dev/null || true)"
        api_ip=""
        if [ -n "$api_node" ]; then
            api_ip="$(kubectl get node "$api_node" -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || true)"
        fi

        if [ -n "$api_ip" ]; then
            token_json="$(curl -s "http://$api_ip:8080/api/v1/auth/token" 2>/dev/null || true)"
            token="$(printf '%s' "$token_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("token",""))' 2>/dev/null || true)"
            if [ -n "$token" ]; then
                state_json="$(curl -s -H "Authorization: Bearer $token" "http://$api_ip:8080/api/v1/state" 2>/dev/null || true)"
                parsed="$(
                    printf '%s' "$state_json" | python3 -c '
import json, sys
d = json.load(sys.stdin)
if not isinstance(d, dict):
    raise SystemExit(1)
nodes = d.get("nodes") or []
links = d.get("links") or []
print("{}|{}|{}|{}".format(
    len(nodes),
    str(bool(d.get("stale", True))).lower(),
    d.get("session_status") or "",
    len(links),
))
' 2>/dev/null || true
                )"
                if [ -n "$parsed" ]; then
                    IFS='|' read -r node_count stale session_status link_count <<< "$parsed"
                    last_observed="nodes=$node_count stale=$stale session_status=$session_status links=$link_count api=http://$api_ip:8080"
                    if [ "$node_count" = "$expected_nodes" ] \
                        && [ "$stale" = "false" ] \
                        && [ "$session_status" = "ready" ]; then
                        echo ""
                        echo "[session] VS-API ready: $node_count nodes, $link_count links, stale=false."
                        return 0
                    fi
                fi
            fi
        fi

        sleep 2
        elapsed=$((elapsed + 2))
        printf '\r[session]   VS-API state: %s (%ss/%ss)' \
            "${last_observed:-not reachable yet}" \
            "$elapsed" \
            "$timeout"
    done

    echo ""
    echo "[session] ERROR: VS-API did not publish current non-stale session state after ${timeout}s" >&2
    if [ -n "${last_observed:-}" ]; then
        echo "[session] Last observed VS-API state: $last_observed" >&2
    fi
    exit 1
}

verify_session_placement() {
    local policy="$1"
    local expected_pods="$2"
    local ready_node_csv expected_placement_nodes actual_placement_nodes distribution

    ready_node_csv="$(
        kubectl get nodes -l nodalarc.io/node-agent=true --no-headers 2>/dev/null \
            | awk '$2 == "Ready" {print $1}' \
            | sort \
            | paste -sd, -
    )"
    if [ -z "$ready_node_csv" ]; then
        echo "[session] ERROR: no Ready nodes with label nodalarc.io/node-agent=true; cannot verify placement" >&2
        exit 1
    fi

    if ! expected_placement_nodes="$(
        PYTHONPATH=lib uv run python -c '
import sys
import importlib.util

spec = importlib.util.spec_from_file_location("session_deployer", "services/nodalarc_operator/session_deployer.py")
session_deployer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(session_deployer)

available_nodes = [n for n in sys.argv[2].split(",") if n]
print(session_deployer.compute_expected_placement_node_count(
    {"sessionYaml": open(sys.argv[1], encoding="utf-8").read()},
    available_nodes,
))
' "$DEFAULT_SESSION" "$ready_node_csv"
    )"; then
        echo "[session] ERROR: failed to compute expected placement for $DEFAULT_SESSION" >&2
        exit 1
    fi
    if ! [[ "$expected_placement_nodes" =~ ^[0-9]+$ ]] || [ "$expected_placement_nodes" -le 0 ]; then
        echo "[session] ERROR: expected placement node count was invalid: $expected_placement_nodes" >&2
        exit 1
    fi

    actual_placement_nodes="$(
        kubectl get pods -n "$NAMESPACE" -l nodalarc.io/node-id -o wide --no-headers 2>/dev/null \
            | awk '{seen[$7] = 1} END {print length(seen)+0}'
    )"
    distribution="$(
        kubectl get pods -n "$NAMESPACE" -l nodalarc.io/node-id -o wide --no-headers 2>/dev/null \
            | awk '{counts[$7]++} END {for (node in counts) print node "=" counts[node]}' \
            | sort \
            | tr '\n' ',' \
            | sed 's/,$//; s/,/, /g'
    )"

    if [ "$actual_placement_nodes" != "$expected_placement_nodes" ]; then
        echo "[session] ERROR: placement policy $policy expected session pods on $expected_placement_nodes node(s), but live pods are on $actual_placement_nodes: ${distribution:-unknown}" >&2
        exit 1
    fi

    echo "[session] Placement verified: policy=$policy nodes=$actual_placement_nodes distribution=${distribution:-unknown}"
}

if ! kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
    echo "[session] ERROR: namespace $NAMESPACE does not exist. Run: make install" >&2
    exit 1
fi

echo "[session] Starting: $DEFAULT_SESSION"
echo "[session] Waiting for CRD (timeout 60s)..."
waited=0
while ! kubectl get crd constellationspecs.nodalarc.io >/dev/null 2>&1; do
    sleep 2
    waited=$((waited + 2))
    printf '\r[session]   Waiting for Operator to register CRD... (%ss)' "$waited"
    if [ "$waited" -ge 60 ]; then
        echo ""
        echo "[session] ERROR: CRD not registered after 60s. Is the Operator running?" >&2
        exit 1
    fi
done
if [ "$waited" -gt 0 ]; then
    echo ""
fi

desired_yaml="$(cat "$DEFAULT_SESSION")"
previous_generation=""
previous_yaml=""
previous_phase=""
previous_observed_generation=""
if kubectl get constellationspec current-session -n "$NAMESPACE" >/dev/null 2>&1; then
    previous_generation="$(kubectl get constellationspec current-session -n "$NAMESPACE" -o jsonpath='{.metadata.generation}')"
    previous_yaml="$(kubectl get constellationspec current-session -n "$NAMESPACE" -o jsonpath='{.spec.sessionYaml}')"
    previous_phase="$(kubectl get constellationspec current-session -n "$NAMESPACE" -o jsonpath='{.status.phase}' 2>/dev/null || true)"
    previous_observed_generation="$(kubectl get constellationspec current-session -n "$NAMESPACE" -o jsonpath='{.status.observedGeneration}' 2>/dev/null || true)"
    if [ "$previous_yaml" = "$desired_yaml" ] \
        && [ "$previous_phase" = "Error" ] \
        && [ "$previous_observed_generation" = "$previous_generation" ]; then
        echo "[session] Previous attempt is terminal Error for the same YAML; recreating CR for fresh reconciliation."
        kubectl delete constellationspec current-session -n "$NAMESPACE" --wait=true
        previous_generation=""
        previous_yaml=""
        previous_phase=""
        previous_observed_generation=""
    fi
fi

tmp_file="$(mktemp)"
trap 'rm -f "$tmp_file"' EXIT
{
    printf 'apiVersion: nodalarc.io/v1alpha1\n'
    printf 'kind: ConstellationSpec\n'
    printf 'metadata:\n'
    printf '  name: current-session\n'
    printf '  namespace: %s\n' "$NAMESPACE"
    printf 'spec:\n'
    printf '  sessionYaml: |\n'
    sed 's/^/    /' "$DEFAULT_SESSION"
} > "$tmp_file"
kubectl apply -f "$tmp_file"

target_generation=""
if [ -n "$previous_generation" ] && [ "$previous_yaml" = "$desired_yaml" ]; then
    target_generation="$previous_generation"
else
    echo "[session] Waiting for CR generation to reflect desired session (timeout 60s)..."
    waited=0
    while [ "$waited" -lt 60 ]; do
        current_generation="$(kubectl get constellationspec current-session -n "$NAMESPACE" -o jsonpath='{.metadata.generation}' 2>/dev/null || true)"
        current_yaml="$(kubectl get constellationspec current-session -n "$NAMESPACE" -o jsonpath='{.spec.sessionYaml}' 2>/dev/null || true)"
        if [ -n "$current_generation" ] && [ "$current_yaml" = "$desired_yaml" ]; then
            if [ -z "$previous_generation" ] || [ "$current_generation" != "$previous_generation" ]; then
                target_generation="$current_generation"
                break
            fi
        fi
        sleep 1
        waited=$((waited + 1))
        printf '\r[session]   Waiting for generation update... (%ss/60s)' "$waited"
    done
    if [ -z "$target_generation" ]; then
        echo ""
        echo "[session] ERROR: CR did not reflect desired session generation after 60s" >&2
        exit 1
    fi
    if [ "$waited" -gt 0 ]; then
        echo ""
    fi
fi

echo "[session] Waiting for Ready (timeout 300s)..."
elapsed=0
while [ "$elapsed" -lt 300 ]; do
    status_fields="$(
        kubectl get constellationspec current-session -n "$NAMESPACE" \
            -o jsonpath='{.status.phase}{"|"}{.status.observedGeneration}{"|"}{.status.readyPods}{"|"}{.status.podCount}{"|"}{.status.wiredPods}' \
            2>/dev/null || true
    )"
    IFS='|' read -r phase observed_generation ready_pods pod_count wired_pods <<< "$status_fields"
    phase="${phase:-Unknown}"
    if [ "$phase" = "Ready" ] \
        && [ "$observed_generation" = "$target_generation" ] \
        && [ "$ready_pods" = "$expected_pods" ] \
        && [ "$pod_count" = "$expected_pods" ]; then
        echo ""
        pods="$(kubectl get pods -n "$NAMESPACE" -l nodalarc.io/node-id --no-headers 2>/dev/null | wc -l | tr -d ' ')"
        running="$(kubectl get pods -n "$NAMESPACE" -l nodalarc.io/node-id --no-headers 2>/dev/null | grep -c Running || true)"
        not_running="$(kubectl get pods -n "$NAMESPACE" -l nodalarc.io/node-id --no-headers 2>/dev/null | grep -v Running | grep -v Completed || true)"
        if [ "$pods" != "$expected_pods" ] || [ "$running" != "$expected_pods" ]; then
            echo "[session] ERROR: Phase is Ready but live pod count is stale: $running/$pods running, expected $expected_pods" >&2
            exit 1
        fi
        if [ -n "$not_running" ]; then
            echo "[session] ERROR: Phase is Ready but some session pods are not running:" >&2
            echo "$not_running" >&2
            exit 1
        fi
        wait_platform_ready 120
        verify_session_placement "$placement_policy" "$expected_pods"
        wait_vs_api_session_state "$expected_pods" 120
        echo "[session] Session ready. $running/$pods session pods running."
        echo "[session] Next: make status"
        exit 0
    fi
    if [ "$phase" = "Error" ]; then
        echo ""
        msg="$(kubectl get constellationspec current-session -n "$NAMESPACE" -o jsonpath='{.status.message}' 2>/dev/null || true)"
        echo "[session] ERROR: $msg" >&2
        exit 1
    fi
    sleep 5
    elapsed=$((elapsed + 5))
    pods="$(kubectl get pods -n "$NAMESPACE" -l nodalarc.io/node-id --no-headers 2>/dev/null | wc -l | tr -d ' ')"
    running="$(kubectl get pods -n "$NAMESPACE" -l nodalarc.io/node-id --no-headers 2>/dev/null | grep -c Running || true)"
    printf '\r[session]   Phase: %s, generation %s/%s, status pods %s/%s, live pods %s/%s running (%ss/300s)' \
        "$phase" \
        "${observed_generation:-?}" \
        "$target_generation" \
        "${ready_pods:-?}" \
        "${pod_count:-?}" \
        "$running" \
        "$pods" \
        "$elapsed"
done

echo ""
echo "[session] ERROR: timed out after 300s" >&2
exit 1
