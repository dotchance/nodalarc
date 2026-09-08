#!/usr/bin/env bash
# Install, upgrade, or reinstall the NodalArc platform through one path.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ACTION="${ACTION:-${1:-install}}"
# shellcheck source=scripts/na-lib.sh
. "$ROOT_DIR/scripts/na-lib.sh"
LIB_PREFIX="$ACTION"
NAMESPACE="${NAMESPACE:-nodalarc}"
HELM_RELEASE="${HELM_RELEASE:-nodalarc}"
HELM_CHART="deploy/helm"
PROJECT_VERSION="${PROJECT_VERSION:-}"
KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
export KUBECONFIG

case "$ACTION" in
    install|upgrade|reinstall) ;;
    *)
        echo "[install] ERROR: ACTION must be install, upgrade, or reinstall; got '$ACTION'" >&2
        exit 2
        ;;
esac

if [ -z "$PROJECT_VERSION" ]; then
    PROJECT_VERSION="$(bash "$ROOT_DIR/scripts/na-project-version.sh")"
fi

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

apply_constellationspec_crd() {
    local chart="$1"
    local chart_dir="$chart"
    local crd_path upload_type upload_id_type closure_digest_type file_count_type
    local runtime_release_type runtime_build_type

    if [[ "$chart_dir" != /* ]]; then
        chart_dir="$ROOT_DIR/$chart_dir"
    fi
    crd_path="$chart_dir/crds/constellationspec.yaml"
    if [ ! -f "$crd_path" ]; then
        echo "[$ACTION] ERROR: ConstellationSpec CRD not found at $crd_path" >&2
        exit 2
    fi

    echo "[$ACTION] Applying ConstellationSpec CRD before runtime images..."
    kubectl apply -f "$crd_path"
    kubectl wait --for=condition=Established \
        crd/constellationspecs.nodalarc.io --timeout=60s
    upload_type="$(
        kubectl get crd constellationspecs.nodalarc.io \
            -o jsonpath='{.spec.versions[?(@.name=="v1alpha1")].schema.openAPIV3Schema.properties.spec.properties.catalogUpload.type}'
    )"
    upload_id_type="$(
        kubectl get crd constellationspecs.nodalarc.io \
            -o jsonpath='{.spec.versions[?(@.name=="v1alpha1")].schema.openAPIV3Schema.properties.spec.properties.catalogUpload.properties.upload_id.type}'
    )"
    closure_digest_type="$(
        kubectl get crd constellationspecs.nodalarc.io \
            -o jsonpath='{.spec.versions[?(@.name=="v1alpha1")].schema.openAPIV3Schema.properties.spec.properties.catalogUpload.properties.closure_digest.type}'
    )"
    file_count_type="$(
        kubectl get crd constellationspecs.nodalarc.io \
            -o jsonpath='{.spec.versions[?(@.name=="v1alpha1")].schema.openAPIV3Schema.properties.spec.properties.catalogUpload.properties.file_count.type}'
    )"
    runtime_release_type="$(
        kubectl get crd constellationspecs.nodalarc.io \
            -o jsonpath='{.spec.versions[?(@.name=="v1alpha1")].schema.openAPIV3Schema.properties.status.properties.runtimeRelease.type}'
    )"
    runtime_build_type="$(
        kubectl get crd constellationspecs.nodalarc.io \
            -o jsonpath='{.spec.versions[?(@.name=="v1alpha1")].schema.openAPIV3Schema.properties.status.properties.runtimeBuild.type}'
    )"
    if [ "$upload_type" != "object" ] || [ "$upload_id_type" != "string" ] \
        || [ "$closure_digest_type" != "string" ] || [ "$file_count_type" != "integer" ] \
        || [ "$runtime_release_type" != "string" ] || [ "$runtime_build_type" != "string" ]; then
        echo "[$ACTION] ERROR: served ConstellationSpec schema lacks the exact runtime upload/proof contract" >&2
        exit 1
    fi
}

wait_platform_ready() {
    local timeout="${1:-180}"
    local elapsed=0
    echo "[$ACTION] Waiting for platform pods (timeout ${timeout}s)..."
    while [ "$elapsed" -lt "$timeout" ]; do
        if session_failed "$NAMESPACE"; then
            echo ""
            echo "[$ACTION] ERROR: current-session is invalid; platform rollout cannot prove readiness." >&2
            if [ -n "$SESSION_FAILED_MESSAGE" ]; then
                printf '%s\n' "$SESSION_FAILED_MESSAGE" >&2
            fi
            echo "[$ACTION] Replace it through the normal path: make session DEFAULT_SESSION=<catalog session YAML>" >&2
            return 1
        fi
        if platform_converged "$NAMESPACE"; then
            echo ""
            echo "[$ACTION] Platform ready: $PLATFORM_CONVERGED_SUMMARY."
            return 0
        fi
        sleep 2
        elapsed=$((elapsed + 2))
        printf '\r[%s]   %s (%ss/%ss)' "$ACTION" "$PLATFORM_CONVERGED_SUMMARY" "$elapsed" "$timeout"
    done
    echo ""
    if [ "${DS_DESIRED:-0}" = "0" ]; then
        echo "[$ACTION] ERROR: Node Agent DaemonSet has 0 desired pods." >&2
        echo "[$ACTION] Fix: kubectl label nodes --all nodalarc.io/node-agent=true" >&2
    else
        echo "[$ACTION] ERROR: Platform pods not ready after ${timeout}s." >&2
        printf '%s' "$PLATFORM_PROBLEMS" >&2
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
apply_constellationspec_crd "$HELM_CHART"

mapfile -t image_args < <(bash "$ROOT_DIR/scripts/na-images.sh" helm-image-args)

# Development workload image overrides map the two exact built-in
# placeholder references to this tree's images, matching the Make-owned
# image resolution of every other runtime image. The value is a JSON
# object; helm --set parsing would read its braces and commas as list
# syntax, so it travels as a generated values file. The chart default
# stays empty outside this installation path.
workload_values_file="$(mktemp /tmp/nodalarc-workload-overrides.XXXXXX.yaml)"
trap 'rm -f "$workload_values_file"' EXIT
bash "$ROOT_DIR/scripts/na-images.sh" workload-dev-overrides-values > "$workload_values_file"
image_args+=("--values=$workload_values_file")

helm_args=(
    "--set-string=namespace=$NAMESPACE"
    "--set-string=runtimeRelease=$PROJECT_VERSION"
    "${image_args[@]}"
)

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
