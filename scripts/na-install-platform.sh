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

managed_key_pattern='(^|[[:space:]])--set(-string)?[=[:space:]]*(images\.|imagePullPolicy|buildTag|runtimeRelease|namespace)'

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
    local elapsed=0 deployment_rows total converged
    local ds_generation ds_observed ds_desired ds_current ds_updated ds_ready ds_available
    local ds_misscheduled session_phase session_message

    echo "[$ACTION] Waiting for platform pods (timeout ${timeout}s)..."
    while [ "$elapsed" -lt "$timeout" ]; do
        deployment_rows="$(
            kubectl get deployments -n "$NAMESPACE" --no-headers \
                -o custom-columns=GEN:.metadata.generation,OBS:.status.observedGeneration,DES:.spec.replicas,TOTAL:.status.replicas,UPD:.status.updatedReplicas,READY:.status.readyReplicas,AVAIL:.status.availableReplicas,TERM:.status.terminatingReplicas \
                2>/dev/null || true
        )"
        total="$(printf '%s\n' "$deployment_rows" | awk 'NF {count++} END {print count+0}')"
        converged="$(
            printf '%s\n' "$deployment_rows" \
                | awk '$1 == $2 && $3 == $4 && $3 == $5 && $3 == $6 && $3 == $7 && ($8 == "<none>" || $8 == 0) {count++} END {print count+0}'
        )"
        ds_generation="$(kubectl get ds nodalarc-node-agent -n "$NAMESPACE" -o jsonpath='{.metadata.generation}' 2>/dev/null || true)"
        ds_observed="$(kubectl get ds nodalarc-node-agent -n "$NAMESPACE" -o jsonpath='{.status.observedGeneration}' 2>/dev/null || true)"
        ds_desired="$(kubectl get ds nodalarc-node-agent -n "$NAMESPACE" -o jsonpath='{.status.desiredNumberScheduled}' 2>/dev/null || echo 0)"
        ds_current="$(kubectl get ds nodalarc-node-agent -n "$NAMESPACE" -o jsonpath='{.status.currentNumberScheduled}' 2>/dev/null || echo 0)"
        ds_updated="$(kubectl get ds nodalarc-node-agent -n "$NAMESPACE" -o jsonpath='{.status.updatedNumberScheduled}' 2>/dev/null || echo 0)"
        ds_ready="$(kubectl get ds nodalarc-node-agent -n "$NAMESPACE" -o jsonpath='{.status.numberReady}' 2>/dev/null || echo 0)"
        ds_available="$(kubectl get ds nodalarc-node-agent -n "$NAMESPACE" -o jsonpath='{.status.numberAvailable}' 2>/dev/null || echo 0)"
        ds_misscheduled="$(kubectl get ds nodalarc-node-agent -n "$NAMESPACE" -o jsonpath='{.status.numberMisscheduled}' 2>/dev/null || echo 0)"
        ds_generation="${ds_generation:-0}"
        ds_observed="${ds_observed:-0}"
        ds_desired="${ds_desired:-0}"
        ds_current="${ds_current:-0}"
        ds_updated="${ds_updated:-0}"
        ds_ready="${ds_ready:-0}"
        ds_available="${ds_available:-0}"
        ds_misscheduled="${ds_misscheduled:-0}"

        session_phase="$(
            kubectl get constellationspec current-session -n "$NAMESPACE" \
                -o jsonpath='{.status.phase}' 2>/dev/null || true
        )"
        if [ "$session_phase" = "Error" ]; then
            session_message="$(
                kubectl get constellationspec current-session -n "$NAMESPACE" \
                    -o jsonpath='{.status.message}' 2>/dev/null || true
            )"
            echo ""
            echo "[$ACTION] ERROR: current-session is invalid; platform rollout cannot prove readiness." >&2
            if [ -n "$session_message" ]; then
                printf '%s\n' "$session_message" >&2
            fi
            echo "[$ACTION] Replace it through the normal path: make session DEFAULT_SESSION=<catalog session YAML>" >&2
            return 1
        fi

        if [ "$total" -gt 0 ] && [ "$converged" -eq "$total" ] \
            && [ "$ds_generation" -eq "$ds_observed" ] \
            && [ "$ds_current" -eq "$ds_desired" ] \
            && [ "$ds_updated" -eq "$ds_desired" ] \
            && [ "$ds_ready" -eq "$ds_desired" ] \
            && [ "$ds_available" -eq "$ds_desired" ] \
            && [ "$ds_misscheduled" -eq 0 ] \
            && [ "$ds_desired" -gt 0 ]; then
            echo ""
            echo "[$ACTION] Platform ready: $total deployments converged, $ds_ready/$ds_desired Node Agent pods ready."
            return 0
        fi

        sleep 2
        elapsed=$((elapsed + 2))
        printf '\r[%s]   Deployments: %s/%s converged, Node Agents: %s/%s updated, %s/%s ready (%ss/%ss)' \
            "$ACTION" "$converged" "$total" "$ds_updated" "$ds_desired" "$ds_ready" "$ds_desired" "$elapsed" "$timeout"
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
apply_constellationspec_crd "$HELM_CHART"

mapfile -t image_args < <(bash "$ROOT_DIR/scripts/na-images.sh" helm-image-args)
extra_args=()
if [ -n "$HELM_EXTRA_ARGS" ]; then
    read -r -a extra_args <<< "$HELM_EXTRA_ARGS"
fi

helm_args=(
    "--set-string=namespace=$NAMESPACE"
    "--set-string=runtimeRelease=$PROJECT_VERSION"
)
if [ "$ALLOW_IMAGE_ARG_OVERRIDE" = "1" ]; then
    helm_args+=("${image_args[@]}" "${extra_args[@]}")
else
    helm_args+=("${extra_args[@]}" "${image_args[@]}")
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
