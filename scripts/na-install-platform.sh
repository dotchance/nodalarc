#!/usr/bin/env bash
# Install, upgrade, or reinstall the NodalArc platform through one path.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ACTION="${ACTION:-${1:-install}}"
NAMESPACE="${NAMESPACE:-nodalarc}"
HELM_RELEASE="${HELM_RELEASE:-nodalarc}"
HELM_CHART="${HELM_CHART:-deploy/helm}"
HELM_EXTRA_ARGS="${HELM_EXTRA_ARGS:-}"
PROJECT_VERSION="${PROJECT_VERSION:-}"
ALLOW_IMAGE_ARG_OVERRIDE="${ALLOW_IMAGE_ARG_OVERRIDE:-0}"
KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
export KUBECONFIG

managed_key_pattern='(^|[[:space:]])--set(-string)?[=[:space:]]*(images\.|imagePullPolicy|buildTag)'

if [ -n "$HELM_EXTRA_ARGS" ] && [[ "$HELM_EXTRA_ARGS" =~ $managed_key_pattern ]]; then
    if [ "$ALLOW_IMAGE_ARG_OVERRIDE" != "1" ]; then
        echo "[install] ERROR: HELM_EXTRA_ARGS overrides managed runtime image values." >&2
        echo "[install] Runtime images are owned by scripts/na-images.sh. Set ALLOW_IMAGE_ARG_OVERRIDE=1 only for explicit diagnostics." >&2
        exit 2
    fi
    echo "[install] Runtime image contract bypassed by ALLOW_IMAGE_ARG_OVERRIDE=1." >&2
fi

case "$ACTION" in
    install|upgrade|reinstall) ;;
    *)
        echo "[install] ERROR: ACTION must be install, upgrade, or reinstall; got '$ACTION'" >&2
        exit 2
        ;;
esac

release_exists() {
    helm status "$HELM_RELEASE" -n "$NAMESPACE" >/dev/null 2>&1
}

namespace_exists() {
    kubectl get namespace "$NAMESPACE" >/dev/null 2>&1
}

render_chart_if_needed() {
    local chart="$1"
    local chart_dir="$chart"

    if [[ "$chart_dir" != /* ]]; then
        chart_dir="$ROOT_DIR/$chart_dir"
    fi

    if [ -f "$chart_dir/Chart.yaml.in" ]; then
        PROJECT_VERSION="$PROJECT_VERSION" bash "$ROOT_DIR/scripts/na-render-helm-chart.sh" "$chart"
        return 0
    fi

    printf '%s\n' "$chart"
}

wait_platform_ready() {
    local timeout="${1:-180}"
    local elapsed=0 total avail ds_desired ds_ready

    echo "[$ACTION] Waiting for platform pods (timeout ${timeout}s)..."
    while [ "$elapsed" -lt "$timeout" ]; do
        total="$(kubectl get deployments -n "$NAMESPACE" --no-headers 2>/dev/null | wc -l | tr -d ' ')"
        avail="$(kubectl get deployments -n "$NAMESPACE" --no-headers 2>/dev/null | awk '{if ($4+0 >= 1) c++} END {print c+0}')"
        ds_desired="$(kubectl get ds nodalarc-node-agent -n "$NAMESPACE" -o jsonpath='{.status.desiredNumberScheduled}' 2>/dev/null || echo 0)"
        ds_ready="$(kubectl get ds nodalarc-node-agent -n "$NAMESPACE" -o jsonpath='{.status.numberReady}' 2>/dev/null || echo 0)"

        if [ "$total" -gt 0 ] && [ "$avail" -eq "$total" ] && [ "$ds_ready" -eq "$ds_desired" ] && [ "$ds_desired" -gt 0 ]; then
            echo ""
            echo "[$ACTION] Platform ready: $total deployments available, $ds_ready/$ds_desired Node Agent pods running."
            return 0
        fi

        sleep 2
        elapsed=$((elapsed + 2))
        printf '\r[%s]   Deployments: %s/%s available, Node Agents: %s/%s ready (%ss/%ss)' \
            "$ACTION" "$avail" "$total" "$ds_ready" "$ds_desired" "$elapsed" "$timeout"
    done

    echo ""
    if [ "${ds_desired:-0}" = "0" ]; then
        echo "[$ACTION] ERROR: Node Agent DaemonSet has 0 desired pods." >&2
        echo "[$ACTION] Fix: kubectl label nodes --all nodalarc.io/node-agent=true" >&2
    else
        echo "[$ACTION] ERROR: Platform pods not ready after ${timeout}s." >&2
        kubectl get pods -n "$NAMESPACE" --no-headers 2>/dev/null | grep -v Running | grep -v Completed || true
    fi
    return 1
}

if [ "$ACTION" = "reinstall" ]; then
    echo "[reinstall] Running official teardown before install..."
    NAMESPACE="$NAMESPACE" bash "$ROOT_DIR/scripts/na-teardown.sh"
    ACTION="install"
fi

if [ "$ACTION" = "install" ]; then
    if release_exists || namespace_exists; then
        echo "[install] ERROR: existing release or namespace found for '$NAMESPACE'." >&2
        echo "[install] Run 'make reinstall' for a destructive reinstall or 'make teardown' first." >&2
        exit 1
    fi
elif [ "$ACTION" = "upgrade" ]; then
    if ! release_exists || ! namespace_exists; then
        echo "[upgrade] ERROR: release and namespace must already exist." >&2
        echo "[upgrade] Run 'make install' first." >&2
        exit 1
    fi
fi

bash "$ROOT_DIR/scripts/na-image-preflight.sh"
HELM_CHART="$(render_chart_if_needed "$HELM_CHART")"

mapfile -t image_args < <(bash "$ROOT_DIR/scripts/na-images.sh" helm-image-args)
extra_args=()
if [ -n "$HELM_EXTRA_ARGS" ]; then
    read -r -a extra_args <<< "$HELM_EXTRA_ARGS"
fi

helm_args=()
if [ "$ALLOW_IMAGE_ARG_OVERRIDE" = "1" ]; then
    helm_args=("${image_args[@]}" "${extra_args[@]}")
else
    helm_args=("${extra_args[@]}" "${image_args[@]}")
fi

mapfile -t node_agent_ips < <(
    kubectl get nodes -l nodalarc.io/node-agent=true \
        -o jsonpath='{range .items[*]}{.status.addresses[?(@.type=="InternalIP")].address}{"\n"}{end}' \
        2>/dev/null | sed '/^[[:space:]]*$/d'
)
if [ "${#node_agent_ips[@]}" -gt 0 ]; then
    echo "[$ACTION] Allowing NATS ingress from ${#node_agent_ips[@]} Node Agent host-network node IP(s)."
    for idx in "${!node_agent_ips[@]}"; do
        ip="${node_agent_ips[$idx]}"
        if [[ "$ip" == *:* ]]; then
            cidr="${ip}/128"
        else
            cidr="${ip}/32"
        fi
        helm_args+=("--set-string=nats.networkPolicy.hostNetworkCIDRs[$idx]=$cidr")
    done
fi

nodal_node="$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
if [ -n "$nodal_node" ]; then
    echo "[$ACTION] Auto-detected node: $nodal_node"
    helm_args+=("--set-string=controlPlaneNode=$nodal_node" "--set-string=sessionNodeName=$nodal_node")
    nats_host="$(
        kubectl get node "$nodal_node" \
            -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || true
    )"
    if [ -n "$nats_host" ]; then
        echo "[$ACTION] Exposing NATS host-network endpoint at ${nats_host}:4222."
        helm_args+=("--set-string=nats.hostNetworkHost=$nats_host")
    fi
fi

if [ "$ACTION" = "install" ]; then
    echo "[install] Installing Helm chart..."
    helm install "$HELM_RELEASE" "$HELM_CHART" --namespace "$NAMESPACE" --create-namespace "${helm_args[@]}"
    wait_platform_ready 180
    echo "[install] Next: make session"
else
    echo "[upgrade] Upgrading Helm release..."
    helm upgrade "$HELM_RELEASE" "$HELM_CHART" --namespace "$NAMESPACE" "${helm_args[@]}"
    wait_platform_ready 120
    echo "[upgrade] Next: make status"
fi
