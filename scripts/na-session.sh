#!/usr/bin/env bash
# Start or replace the current NodalArc session.

set -euo pipefail

NAMESPACE="${NAMESPACE:-nodalarc}"
DEFAULT_SESSION="${DEFAULT_SESSION:-catalog/nodalarc/sessions/earth-leo-simple.yaml}"
PLATFORM_CONFIG="${PLATFORM_CONFIG:-configs/platform.yaml}"
KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
SHIPPED_CATALOG_ROOT="${SHIPPED_CATALOG_ROOT:-catalog/nodalarc}"
export KUBECONFIG

if [ ! -f "$DEFAULT_SESSION" ]; then
    echo "[session] ERROR: session file does not exist: $DEFAULT_SESSION" >&2
    exit 1
fi

if ! session_ref="$(
    PYTHONPATH=lib uv run python -c '
from pathlib import Path
import sys

session_path = Path(sys.argv[1]).resolve(strict=True)
catalog_root = Path(sys.argv[2]).resolve(strict=True)
try:
    relative = session_path.relative_to(catalog_root)
except ValueError:
    raise SystemExit(
        f"session must be a shipped catalog file under {catalog_root}: {session_path}"
    )
if len(relative.parts) < 2 or relative.parts[0] != "sessions":
    raise SystemExit(f"session must be under the sessions catalog family: {relative}")
if relative.suffix.lower() not in {".yaml", ".yml"}:
    raise SystemExit(f"session must be YAML: {relative}")
print(f"nodalarc:{relative.as_posix()}")
' "$DEFAULT_SESSION" "$SHIPPED_CATALOG_ROOT"
)"; then
    echo "[session] ERROR: DEFAULT_SESSION must identify a shipped catalog session" >&2
    exit 1
fi
echo "[session] Catalog session: $session_ref"

server_yaml_file="$(mktemp)"
response_file="$(mktemp)"
pods_json_file="$(mktemp)"
trap 'rm -f "$server_yaml_file" "$response_file" "$pods_json_file"' EXIT

echo "[session] Computing placement policy..."
if ! placement_policy="$(PYTHONPATH=lib uv run python -c 'import sys; from pathlib import Path; from nodalarc.platform_config import init_platform_config; cfg = init_platform_config(Path(sys.argv[1])); print(cfg.default_session_pod_placement_policy)' "$PLATFORM_CONFIG")"; then
    echo "[session] ERROR: failed to read placement policy from $PLATFORM_CONFIG" >&2
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

discover_vs_api() {
    local timeout="${1:-120}"
    local elapsed=0 api_node api_ip token_json

    echo "[session] Discovering VS-API (timeout ${timeout}s)..."
    while [ "$elapsed" -lt "$timeout" ]; do
        api_node="$(kubectl get pod -n "$NAMESPACE" -l app=nodalarc-vs-api -o jsonpath='{.items[0].spec.nodeName}' 2>/dev/null || true)"
        api_ip=""
        if [ -n "$api_node" ]; then
            api_ip="$(kubectl get node "$api_node" -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || true)"
        fi
        if [ -n "$api_ip" ]; then
            token_json="$(curl -fsS "http://$api_ip:8080/api/v1/auth/token" 2>/dev/null || true)"
            api_token="$(
                printf '%s' "$token_json" \
                    | python3 -c 'import json, sys; print(json.load(sys.stdin).get("token", ""))' \
                        2>/dev/null || true
            )"
            if [ -n "$api_token" ]; then
                api_base="http://$api_ip:8080"
                echo "[session] VS-API ready: $api_base"
                return 0
            fi
        fi
        sleep 2
        elapsed=$((elapsed + 2))
        printf '\r[session]   VS-API not reachable yet (%ss/%ss)' "$elapsed" "$timeout"
    done

    echo ""
    echo "[session] ERROR: VS-API was not reachable after ${timeout}s" >&2
    exit 1
}

wait_vs_api_session_state() {
    local expected_nodes="$1"
    local timeout="${2:-120}"
    local elapsed=0 state_json parsed
    local node_count stale session_status link_count last_observed

    echo "[session] Waiting for VS-API state to match the active session (timeout ${timeout}s)..."
    while [ "$elapsed" -lt "$timeout" ]; do
        state_json="$(
            curl -fsS \
                -H "Authorization: Bearer $api_token" \
                "$api_base/api/v1/state" \
                2>/dev/null || true
        )"
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
            last_observed="nodes=$node_count stale=$stale session_status=$session_status links=$link_count api=$api_base"
            if [ "$node_count" = "$expected_nodes" ] \
                && [ "$stale" = "false" ] \
                && [ "$session_status" = "ready" ]; then
                echo ""
                echo "[session] VS-API ready: $node_count nodes, $link_count links, stale=false."
                return 0
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

    if ! kubectl get pods -n "$NAMESPACE" -l nodalarc.io/node-id -o json > "$pods_json_file"; then
        echo "[session] ERROR: failed to read live session pod placement" >&2
        exit 1
    fi

    if ! expected_placement_nodes="$(
        PYTHONPATH=lib:services uv run python -c '
import sys
import json
from pathlib import Path

from nodalarc.platform_config import compute_pod_placement, init_platform_config

pods = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
available_nodes = [node for node in sys.argv[2].split(",") if node]
config = init_platform_config(Path(sys.argv[3]))
node_vars = {}
for pod in pods.get("items", []):
    labels = pod.get("metadata", {}).get("labels", {})
    node_id = labels.get("nodalarc.io/node-id")
    if not node_id:
        continue
    if "nodalarc.io/gs-name" in labels:
        node_vars[node_id] = {
            "node_type": "ground_station",
            "gs_name": labels["nodalarc.io/gs-name"],
        }
    else:
        values = {"node_type": "satellite"}
        if "nodalarc.io/plane" in labels:
            values["plane"] = int(labels["nodalarc.io/plane"])
        node_vars[node_id] = values

if len(node_vars) != int(sys.argv[4]):
    raise SystemExit(
        f"live session pod inventory has {len(node_vars)} nodes; expected {sys.argv[4]}"
    )
placement = compute_pod_placement(
    {
        "policy": config.default_session_pod_placement_policy,
        "planes_per_group": config.default_session_pod_planes_per_group,
    },
    node_vars,
    available_nodes,
)
print(len(set(placement.values())))
' "$pods_json_file" "$ready_node_csv" "$PLATFORM_CONFIG" "$expected_pods"
    )"; then
        echo "[session] ERROR: failed to compute expected placement from live pod identities" >&2
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

discover_vs_api 120

echo "[session] Reviewing the installed catalog closure..."
if ! http_status="$(
    curl -sS \
        -o "$response_file" \
        -w '%{http_code}' \
        -H "Authorization: Bearer $api_token" \
        "$api_base/api/v1/sessions"
)"; then
    echo "[session] ERROR: failed to list catalog sessions through VS-API" >&2
    exit 1
fi
if [ "$http_status" != "200" ]; then
    echo "[session] ERROR: VS-API session listing returned HTTP $http_status" >&2
    exit 1
fi
if ! session_fields="$(
    python3 -c '
import json
import sys

sessions = json.load(sys.stdin)
requested = sys.argv[1]
matches = [
    item
    for item in sessions
    if item.get("source_id", {}).get("session_ref") == requested
]
if len(matches) != 1:
    raise SystemExit(f"expected one catalog session {requested}, found {len(matches)}")
item = matches[0]
blockers = "; ".join(
    blocker.get("message", "catalog session is blocked")
    for blocker in item.get("blockers", [])
).replace("|", "/")
print("|".join((
    str(bool(item.get("deploy_allowed"))).lower(),
    item.get("source_revision") or "",
    item.get("document_digest") or "",
    item.get("dependency_digest") or "",
    blockers,
)))
' "$session_ref" < "$response_file"
)"; then
    echo "[session] ERROR: installed VS-API catalog does not contain $session_ref" >&2
    exit 1
fi
IFS='|' read -r deploy_allowed source_revision document_digest dependency_digest blockers \
    <<< "$session_fields"
if [ "$deploy_allowed" != "true" ]; then
    echo "[session] ERROR: $session_ref is not deployable: ${blockers:-validation failed}" >&2
    exit 1
fi
if [ -z "$source_revision" ] || [ -z "$document_digest" ] || [ -z "$dependency_digest" ]; then
    echo "[session] ERROR: VS-API did not return reviewed catalog identities for $session_ref" >&2
    exit 1
fi

if ! local_digests="$(
    PYTHONPATH=lib uv run python -c '
from pathlib import Path
import sys

from nodalarc.catalog_closure import CatalogClosureCollector, FilesystemCatalogReadView
from nodalarc.catalog_paths import CatalogRoots

root_yaml = Path(sys.argv[1]).read_bytes()
view = FilesystemCatalogReadView(CatalogRoots.from_catalog_root(sys.argv[2]))
closure = CatalogClosureCollector.collect(root_yaml, view)
print(f"{closure.document_digest}|{closure.closure_digest}")
' "$DEFAULT_SESSION" "$SHIPPED_CATALOG_ROOT"
)"; then
    echo "[session] ERROR: failed to validate the local catalog closure for $session_ref" >&2
    exit 1
fi
IFS='|' read -r local_document_digest local_dependency_digest <<< "$local_digests"
if [ "$local_document_digest" != "$document_digest" ] \
    || [ "$local_dependency_digest" != "$dependency_digest" ]; then
    echo "[session] ERROR: installed VS-API catalog content differs from this checkout" >&2
    echo "[session] Deploy the current VS-API image before switching $session_ref." >&2
    exit 1
fi

if ! http_status="$(
    curl -sS \
        -o "$server_yaml_file" \
        -w '%{http_code}' \
        -G \
        -H "Authorization: Bearer $api_token" \
        --data-urlencode "session_ref=$session_ref" \
        "$api_base/api/v1/sessions/yaml"
)"; then
    echo "[session] ERROR: failed to download reviewed session YAML from VS-API" >&2
    exit 1
fi
if [ "$http_status" != "200" ] || ! cmp -s "$DEFAULT_SESSION" "$server_yaml_file"; then
    echo "[session] ERROR: VS-API did not return the exact selected root YAML" >&2
    exit 1
fi

switch_payload="$(
    python3 -c '
import json
import sys

payload = {
    "source": {"kind": "catalog", "session_ref": sys.argv[1]},
    "expected_source_revision": sys.argv[2],
    "expected_document_digest": sys.argv[3],
    "expected_dependency_digest": sys.argv[4],
}
print(json.dumps(payload, separators=(",", ":")))
' "$session_ref" "$source_revision" "$document_digest" "$dependency_digest"
)"
echo "[session] Requesting a guarded session switch through VS-API..."
if ! http_status="$(
    curl -sS \
        -o "$response_file" \
        -w '%{http_code}' \
        -X POST \
        -H "Authorization: Bearer $api_token" \
        -H 'Content-Type: application/json' \
        --data "$switch_payload" \
        "$api_base/api/v1/sessions/switch"
)"; then
    echo "[session] ERROR: VS-API session switch request failed" >&2
    exit 1
fi
if [ "$http_status" != "200" ]; then
    api_error="$(
        python3 -c '
import json
import sys

value = json.load(sys.stdin)
print(value.get("error") or value.get("detail") or "session switch was refused")
' < "$response_file" 2>/dev/null || printf 'HTTP %s' "$http_status"
    )"
    echo "[session] ERROR: $api_error" >&2
    exit 1
fi
if ! operation_id="$(
    python3 -c '
import json
import sys

value = json.load(sys.stdin)
operation_id = value.get("operation_id")
if not isinstance(operation_id, str) or not operation_id:
    raise SystemExit("missing operation_id")
print(operation_id)
' < "$response_file"
)"; then
    echo "[session] ERROR: VS-API accepted the switch without an operation ID" >&2
    exit 1
fi

echo "[session] Waiting for transition $operation_id (timeout 360s)..."
elapsed=0
target_generation=""
while [ "$elapsed" -lt 360 ]; do
    if ! http_status="$(
        curl -sS \
            -o "$response_file" \
            -w '%{http_code}' \
            -H "Authorization: Bearer $api_token" \
            "$api_base/api/v1/session-transitions/$operation_id"
    )"; then
        echo ""
        echo "[session] ERROR: failed to query transition $operation_id" >&2
        exit 1
    fi
    if [ "$http_status" != "200" ]; then
        echo ""
        echo "[session] ERROR: transition query returned HTTP $http_status" >&2
        exit 1
    fi
    if ! operation_fields="$(
        python3 -c '
import json
import sys

value = json.load(sys.stdin)
runtime = value.get("runtime") or {}
failure = value.get("failure") or {}
message = str(failure.get("message") or "").replace("|", "/")
print("|".join((
    str(value.get("state") or ""),
    str(runtime.get("session_id") or ""),
    str(runtime.get("generation") or ""),
    str(failure.get("code") or ""),
    message,
)))
' < "$response_file"
    )"; then
        echo ""
        echo "[session] ERROR: transition response was invalid" >&2
        exit 1
    fi
    IFS='|' read -r transition_state runtime_session_id target_generation failure_code failure_message \
        <<< "$operation_fields"
    case "$transition_state" in
        succeeded)
            echo ""
            echo "[session] Transition succeeded: session=$runtime_session_id generation=$target_generation"
            break
            ;;
        failed|cancelled)
            echo ""
            echo "[session] ERROR: ${failure_message:-transition $transition_state} (${failure_code:-unknown})" >&2
            exit 1
            ;;
    esac
    sleep 2
    elapsed=$((elapsed + 2))
    printf '\r[session]   Transition: %s (%ss/360s)' "${transition_state:-unknown}" "$elapsed"
done
if [ -z "$target_generation" ]; then
    echo ""
    echo "[session] ERROR: transition $operation_id did not complete after 360s" >&2
    exit 1
fi

status_fields="$(
    kubectl get constellationspec current-session -n "$NAMESPACE" \
        -o jsonpath='{.metadata.generation}{"|"}{.status.phase}{"|"}{.status.observedGeneration}{"|"}{.status.readyPods}{"|"}{.status.podCount}{"|"}{.status.wiredPods}' \
        2>/dev/null || true
)"
IFS='|' read -r current_generation phase observed_generation ready_pods pod_count wired_pods \
    <<< "$status_fields"
expected_pods="$pod_count"
if ! [[ "$expected_pods" =~ ^[0-9]+$ ]] || [ "$expected_pods" -le 0 ]; then
    echo "[session] ERROR: Ready transition reported invalid pod count: ${expected_pods:-missing}" >&2
    exit 1
fi
if [ "$current_generation" != "$target_generation" ] \
    || [ "$observed_generation" != "$target_generation" ] \
    || [ "$phase" != "Ready" ] \
    || [ "$ready_pods" != "$expected_pods" ] \
    || [ "$wired_pods" != "$expected_pods" ]; then
    echo "[session] ERROR: ConstellationSpec no longer proves the completed transition" >&2
    exit 1
fi

pods="$(kubectl get pods -n "$NAMESPACE" -l nodalarc.io/node-id --no-headers 2>/dev/null | wc -l | tr -d ' ')"
running="$(kubectl get pods -n "$NAMESPACE" -l nodalarc.io/node-id --no-headers 2>/dev/null | grep -c Running || true)"
not_running="$(kubectl get pods -n "$NAMESPACE" -l nodalarc.io/node-id --no-headers 2>/dev/null | grep -v Running | grep -v Completed || true)"
if [ "$pods" != "$expected_pods" ] || [ "$running" != "$expected_pods" ]; then
    echo "[session] ERROR: live pod count is stale: $running/$pods running, expected $expected_pods" >&2
    exit 1
fi
if [ -n "$not_running" ]; then
    echo "[session] ERROR: some session pods are not running:" >&2
    echo "$not_running" >&2
    exit 1
fi

wait_platform_ready 120
verify_session_placement "$placement_policy" "$expected_pods"
wait_vs_api_session_state "$expected_pods" 120
echo "[session] Session ready. $running/$pods session pods running."
echo "[session] Next: make status"
