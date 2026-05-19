#!/usr/bin/env bash
# Purge NodalArc images from K3s containerd caches.

set -uo pipefail

NAMESPACE="${NAMESPACE:-nodalarc}"
PURGE_SCOPE="${PURGE_SCOPE:-${SCOPE:-all}}" # all|remote|local
REMOTE_REQUIRED="${REMOTE_REQUIRED:-auto}"
LOCAL_REQUIRED="${LOCAL_REQUIRED:-0}"
SUDO_CTR="${SUDO_CTR:-sudo}"
if [ -n "$SUDO_CTR" ]; then
    read -r -a SUDO_CTR_CMD <<< "$SUDO_CTR"
else
    SUDO_CTR_CMD=()
fi

status=0

resolve_local_k3s() {
    if [ -n "${K3S_BIN:-}" ]; then
        [ -x "$K3S_BIN" ] && printf '%s\n' "$K3S_BIN" && return 0
        return 1
    fi
    if command -v k3s >/dev/null 2>&1; then
        command -v k3s
        return 0
    fi
    for candidate in /usr/local/bin/k3s /usr/bin/k3s; do
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

purge_local() {
    echo "[purge-containerd] Purging local K3s containerd..."
    local k3s_bin refs removed
    if ! k3s_bin="$(resolve_local_k3s)"; then
        if [ "$LOCAL_REQUIRED" = "1" ]; then
            echo "  local: failed (k3s command unavailable)" >&2
            status=1
        else
            echo "  local: not-applicable"
        fi
        return
    fi
    if ! refs="$("${SUDO_CTR_CMD[@]}" "$k3s_bin" ctr -n k8s.io images ls -q 2>/dev/null)"; then
        echo "  local: failed (cannot list K3s containerd images)" >&2
        status=1
        return
    fi
    refs="$(printf '%s\n' "$refs" | grep -E '(^|/)nodalarc/' || true)"
    if [ -z "$refs" ]; then
        echo "  local: no NodalArc images"
        return 0
    fi
    removed=0
    while IFS= read -r ref; do
        [ -n "$ref" ] || continue
        echo "  local: remove $ref"
        if "${SUDO_CTR_CMD[@]}" "$k3s_bin" ctr -n k8s.io images rm "$ref" >/dev/null 2>&1; then
            removed=$((removed + 1))
        else
            echo "  local: failed removing $ref" >&2
            status=1
        fi
    done <<< "$refs"
    if [ "$removed" -gt 0 ]; then
        echo "  local: purged"
    fi
}

purge_remote() {
    echo "[purge-containerd] Purging remote K3s containerd through Node Agent pods..."
    local pods required remote_script
    pods="$(kubectl get pods -n "$NAMESPACE" -l app=nodalarc-node-agent \
        --no-headers -o custom-columns=NAME:.metadata.name,NODE:.spec.nodeName 2>/dev/null || true)"
    required="$REMOTE_REQUIRED"
    if [ "$required" = "auto" ]; then
        if kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
            required=1
        else
            required=0
        fi
    fi
    if [ -z "$pods" ]; then
        if [ "$required" = "1" ]; then
            echo "  remote: skipped-unavailable (Node Agent pods required but unavailable)" >&2
            status=1
        else
            echo "  remote: not-applicable"
        fi
        return
    fi

    remote_script='
runtime="${CONTAINER_RUNTIME_ENDPOINT:-unix:///run/k3s/containerd/containerd.sock}"
table="$(crictl --runtime-endpoint "$runtime" images 2>/dev/null)" || {
    echo "list-failed" >&2
    exit 2
}
images="$(printf "%s\n" "$table" | awk '"'"'NR > 1 && $1 ~ /(^|\/)nodalarc\// && $2 != "<none>" {print $1 ":" $2}'"'"' | sort -u)"
if [ -z "$images" ]; then
    echo "none"
    exit 0
fi
failed=0
removed=0
for image in $images; do
    if crictl --runtime-endpoint "$runtime" rmi "$image" >/dev/null 2>&1; then
        removed=$((removed + 1))
    else
        echo "failed:$image" >&2
        failed=1
    fi
done
if [ "$failed" -ne 0 ]; then
    exit 1
fi
echo "purged:$removed"
'

    while IFS= read -r line; do
        [ -n "$line" ] || continue
        local pod node result
        pod="$(echo "$line" | awk '{print $1}')"
        node="$(echo "$line" | awk '{print $2}')"
        if result="$(kubectl exec "$pod" -n "$NAMESPACE" -c node-agent -- \
            sh -c "$remote_script" 2>&1)"; then
            case "$result" in
                none)
                    echo "  remote: $node none"
                    ;;
                purged:*)
                    echo "  remote: $node purged (${result#purged:})"
                    ;;
                *)
                    echo "  remote: $node purged"
                    [ -n "$result" ] && printf '%s\n' "$result" | sed 's/^/    /'
                    ;;
            esac
        else
            echo "  remote: $node failed" >&2
            [ -n "$result" ] && printf '%s\n' "$result" | sed 's/^/    /' >&2
            status=1
        fi
    done <<< "$pods"
}

case "$PURGE_SCOPE" in
    all)
        purge_remote
        purge_local
        ;;
    remote)
        purge_remote
        ;;
    local)
        purge_local
        ;;
    *)
        echo "na-purge-containerd: PURGE_SCOPE must be all, remote, or local" >&2
        exit 2
        ;;
esac

exit "$status"
