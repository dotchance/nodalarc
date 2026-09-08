#!/usr/bin/env bash
# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
#
# Shared lifecycle rules. Sourced by the lifecycle scripts, never executed.
#
#   mode_record_load RECORD      parse na-mode.sh's key=value record into
#                                MODE_RESOLVED, REGISTRY_HOST_RESOLVED,
#                                REGISTRY_PREFIX_RESOLVED, NODE_COUNT,
#                                MIRROR_THIRD_PARTY_RESOLVED (empty values survive)
#   platform_converged NS        0 when every required platform workload named by
#                                the image inventory exists and is converged;
#                                sets PLATFORM_CONVERGED_SUMMARY, PLATFORM_PROBLEMS,
#                                DS_DESIRED, DS_READY
#   session_failed NS            0 when the session CR is in phase Error;
#                                sets SESSION_FAILED_MESSAGE
#   discover_vs_api NS TIMEOUT   sets api_base and api_token from the live VS-API
#                                pod's node address; LIB_PREFIX names the caller
#   release_chart_matches NS RELEASE CHART_DIR
#                                0 when the newest Helm release revision is
#                                deployed and recorded the assembled chart's
#                                digest; sets RELEASE_CHART_DIFF otherwise

NA_LIB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIB_PREFIX="${LIB_PREFIX:-lifecycle}"

mode_record_load() {
    local line key value
    MODE_RESOLVED=""
    REGISTRY_HOST_RESOLVED=""
    REGISTRY_PREFIX_RESOLVED=""
    NODE_COUNT=""
    MIRROR_THIRD_PARTY_RESOLVED=""
    while IFS= read -r line; do
        [ -n "$line" ] || continue
        key="${line%%=*}"
        value="${line#*=}"
        case "$key" in
            mode) MODE_RESOLVED="$value" ;;
            registry_host) REGISTRY_HOST_RESOLVED="$value" ;;
            registry_prefix) REGISTRY_PREFIX_RESOLVED="$value" ;;
            node_count) NODE_COUNT="$value" ;;
            mirror_third_party) MIRROR_THIRD_PARTY_RESOLVED="$value" ;;
            *)
                echo "[$LIB_PREFIX] ERROR: unknown mode record key: $key" >&2
                return 2
                ;;
        esac
    done <<< "$1"
    if [ -z "$MODE_RESOLVED" ] || [ -z "$NODE_COUNT" ]; then
        echo "[$LIB_PREFIX] ERROR: incomplete mode record" >&2
        return 2
    fi
}

platform_converged() {
    local ns="$1"
    local logical resource source kind name row resources
    local dep_total=0 dep_converged=0
    PLATFORM_PROBLEMS=""
    PLATFORM_CONVERGED_SUMMARY=""
    DS_DESIRED=0
    DS_READY=0
    # The required population comes from the inventory; an inventory that does
    # not answer means nothing was checked, and nothing checked is not converged.
    if ! resources="$(bash "$NA_LIB_ROOT/scripts/na-images.sh" list-platform-resources)" || [ -z "$resources" ]; then
        PLATFORM_PROBLEMS="the image inventory did not answer; no workload was checked"$'\n'
        PLATFORM_CONVERGED_SUMMARY="inventory unavailable"
        return 1
    fi
    while IFS=$'\t' read -r logical resource source; do
        [ -n "$logical" ] || continue
        kind="${resource%%/*}"
        name="${resource#*/}"
        case "$kind" in
            deployment)
                dep_total=$((dep_total + 1))
                row="$(
                    kubectl get deployment "$name" -n "$ns" --no-headers \
                        -o custom-columns=GEN:.metadata.generation,OBS:.status.observedGeneration,DES:.spec.replicas,TOTAL:.status.replicas,UPD:.status.updatedReplicas,READY:.status.readyReplicas,AVAIL:.status.availableReplicas,TERM:.status.terminatingReplicas \
                        2>/dev/null || true
                )"
                if [ -z "$row" ]; then
                    PLATFORM_PROBLEMS+="$logical: $resource is missing"$'\n'
                    continue
                fi
                if printf '%s\n' "$row" | awk '$1 == $2 && $3 == $4 && $3 == $5 && $3 == $6 && $3 == $7 && ($8 == "<none>" || $8 == 0) && $3 > 0 {ok = 1} END {exit ok ? 0 : 1}'; then
                    dep_converged=$((dep_converged + 1))
                else
                    PLATFORM_PROBLEMS+="$logical: $resource is not converged ($row)"$'\n'
                fi
                ;;
            daemonset)
                row="$(
                    kubectl get daemonset "$name" -n "$ns" --no-headers \
                        -o custom-columns=GEN:.metadata.generation,OBS:.status.observedGeneration,DES:.status.desiredNumberScheduled,CURRENT:.status.currentNumberScheduled,UPD:.status.updatedNumberScheduled,READY:.status.numberReady,AVAIL:.status.numberAvailable,MISSCHEDULED:.status.numberMisscheduled \
                        2>/dev/null || true
                )"
                if [ -z "$row" ]; then
                    PLATFORM_PROBLEMS+="$logical: $resource is missing"$'\n'
                    continue
                fi
                DS_DESIRED="$(printf '%s\n' "$row" | awk '{print $3 + 0}')"
                DS_READY="$(printf '%s\n' "$row" | awk '{print $6 + 0}')"
                if ! printf '%s\n' "$row" | awk '$1 == $2 && $3 == $4 && $3 == $5 && $3 == $6 && $3 == $7 && $8 == 0 && $3 > 0 {ok = 1} END {exit ok ? 0 : 1}'; then
                    PLATFORM_PROBLEMS+="$logical: $resource is not converged ($row)"$'\n'
                fi
                ;;
            *)
                PLATFORM_PROBLEMS+="$logical: unsupported resource kind $kind"$'\n'
                ;;
        esac
    done <<< "$resources"
    PLATFORM_CONVERGED_SUMMARY="$dep_converged/$dep_total deployments converged; $DS_READY/$DS_DESIRED Node Agents ready"
    [ "$dep_total" -gt 0 ] && [ -z "$PLATFORM_PROBLEMS" ]
}

release_chart_matches() {
    # release_chart_matches NS RELEASE CHART_DIR -> 0 when the newest revision
    # of the Helm release is deployed and its chart recorded the assembled
    # chart's digest (scripts/na-chart-identity.py). Any other outcome (no
    # release, newest revision not deployed, no recorded digest, unreadable
    # payload, difference) returns 1 with the reason in RELEASE_CHART_DIFF.
    # Reads the release secrets; no helm invocation.
    local ns="$1" release="$2" chart_dir="$3" secrets_file rc=0
    RELEASE_CHART_DIFF=""
    secrets_file="$(mktemp)"
    if ! kubectl get secrets -n "$ns" -l "owner=helm,name=$release" -o json > "$secrets_file" 2>/dev/null; then
        rm -f "$secrets_file"
        RELEASE_CHART_DIFF="Helm release secrets for $release in namespace $ns could not be read"
        return 1
    fi
    RELEASE_CHART_DIFF="$(cd "$NA_LIB_ROOT" && uv run --quiet python scripts/na-chart-identity.py release-check "$chart_dir" "$secrets_file" "$ns" "$release" 2>&1)" || rc=$?
    rm -f "$secrets_file"
    case "$rc" in
        0) return 0 ;;
        3) return 1 ;;
        *) RELEASE_CHART_DIFF="release chart comparison failed (exit $rc): $RELEASE_CHART_DIFF"; return 1 ;;
    esac
}

session_failed() {
    local ns="$1" phase
    SESSION_FAILED_MESSAGE=""
    phase="$(kubectl get constellationspec current-session -n "$ns" -o jsonpath='{.status.phase}' 2>/dev/null || true)"
    [ "$phase" = "Error" ] || return 1
    SESSION_FAILED_MESSAGE="$(kubectl get constellationspec current-session -n "$ns" -o jsonpath='{.status.message}' 2>/dev/null || true)"
    return 0
}

discover_vs_api() {
    # discover_vs_api NS TIMEOUT -> 0 with api_base and api_token set once the
    # VS-API's token endpoint answers from the pod's node address. Elapsed
    # time is wall time (SECONDS), so time spent in requests counts toward
    # the deadline. The deadline is checked before each iteration, so the
    # call may overrun it by one iteration: two kubectl reads, one curl
    # (--max-time 5) and a two-second sleep. --request-timeout bounds one
    # server request per kubectl call, not the kubectl process, so the
    # iteration has no fixed upper bound; it is short in practice.
    local ns="$1"
    local timeout="${2:-120}"
    local started="$SECONDS" elapsed=0 api_node api_ip token_json
    echo "[$LIB_PREFIX] Discovering VS-API (timeout ${timeout}s)..."
    while [ "$elapsed" -lt "$timeout" ]; do
        api_node="$(kubectl get pod -n "$ns" -l app=nodalarc-vs-api --request-timeout=10s -o jsonpath='{.items[0].spec.nodeName}' 2>/dev/null || true)"
        api_ip=""
        if [ -n "$api_node" ]; then
            api_ip="$(kubectl get node "$api_node" --request-timeout=10s -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || true)"
        fi
        if [ -n "$api_ip" ]; then
            token_json="$(curl -fsS --max-time 5 "http://$api_ip:8080/api/v1/auth/token" 2>/dev/null || true)"
            api_token="$(
                printf '%s' "$token_json" \
                    | python3 -c 'import json, sys; print(json.load(sys.stdin).get("token", ""))' \
                        2>/dev/null || true
            )"
            if [ -n "$api_token" ]; then
                api_base="http://$api_ip:8080"
                echo "[$LIB_PREFIX] VS-API ready: $api_base"
                return 0
            fi
        fi
        sleep 2
        elapsed=$((SECONDS - started))
        printf '\r[%s]   VS-API not reachable yet (%ss/%ss)' "$LIB_PREFIX" "$elapsed" "$timeout"
    done
    echo ""
    echo "[$LIB_PREFIX] ERROR: VS-API was not reachable after ${elapsed}s (timeout ${timeout}s)" >&2
    return 1
}
