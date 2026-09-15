#!/usr/bin/env bash
# Copyright 2024-2026 .chance (dotchance)
# na-teardown.sh — Complete NodalArc teardown
# This is the ONLY permitted teardown mechanism. Never use kubectl delete namespace
# as a standalone teardown. Never construct custom teardown sequences.
# This script must be run to completion before any new deploy.

set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/na-lib.sh
. "$ROOT_DIR/scripts/na-lib.sh"
LIB_PREFIX="teardown"
NAMESPACE="${NAMESPACE:-nodalarc}"
KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
export KUBECONFIG

# One private scratch directory for captured diagnostics, created by mktemp
# under the system temp root and validated before use. Nothing else in this
# script removes files: the single EXIT trap re-validates the path it was
# given (non-empty, an absolute path under the temp root, no glob or space
# characters, still a directory) and removes only that directory.
TEARDOWN_TMP_ROOT="${TMPDIR:-/tmp}"
TEARDOWN_TMP="$(mktemp -d "${TEARDOWN_TMP_ROOT%/}/na-teardown.XXXXXXXX")"
case "$TEARDOWN_TMP" in
    "${TEARDOWN_TMP_ROOT%/}"/na-teardown.????????) ;;
    *)
        echo "ERROR: refusing to use scratch directory '$TEARDOWN_TMP' (unexpected shape)" >&2
        exit 1
        ;;
esac
if [ ! -d "$TEARDOWN_TMP" ] || [[ "$TEARDOWN_TMP" == *[\*\?\[\ ]* ]]; then
    echo "ERROR: refusing to use scratch directory '$TEARDOWN_TMP'" >&2
    exit 1
fi
remove_scratch_directory() {
    local dir="${TEARDOWN_TMP:-}"
    case "$dir" in
        "${TEARDOWN_TMP_ROOT%/}"/na-teardown.????????) ;;
        *) return 0 ;;
    esac
    if [ -d "$dir" ] && [[ "$dir" != *[\*\?\[\ ]* ]]; then
        rm -rf -- "$dir"
    fi
}
trap remove_scratch_directory EXIT

echo "=== NodalArc Teardown ==="
echo "Copyright 2024-2026 .chance (dotchance)"
echo "Official source: https://github.com/dotchance/nodalarc"

# The Node Agent's cleaner (python -m node_agent.reconcile --clean) prints one
# JSON report and exits 0 only when it verified the host clean. One parser
# judges every report, remote and local: the required fields and their types
# are checked explicitly, and a host counts as verified only when the cleaner
# exited 0 AND its report is valid and clean. Exit zero alone proves nothing.
read -r -d '' CLEANUP_REPORT_PARSER <<'PY' || true
import json
import sys

label, rc = sys.argv[1], sys.argv[2]
lines = [line for line in sys.stdin.read().splitlines() if line.strip()]
if not lines:
    print(f"{label}: no report")
    sys.exit(1)
try:
    report = json.loads(lines[-1])
except ValueError as exc:
    print(f"{label}: unparseable report: {exc}")
    sys.exit(1)
if not isinstance(report, dict):
    print(f"{label}: invalid report: not an object")
    sys.exit(1)


def field(name, kinds):
    if name not in report or not isinstance(report[name], kinds):
        print(f"{label}: invalid report: field {name!r} missing or of the wrong type")
        sys.exit(1)
    return report[name]


host = field("host", str)
removed = field("removed", list)
failed = field("failed", list)
remaining = field("remaining", list)
verification_completed = field("verification_completed", bool)
for name in ("enumeration_error", "verification_error"):
    if name not in report or not (report[name] is None or isinstance(report[name], str)):
        print(f"{label}: invalid report: field {name!r} missing or of the wrong type")
        sys.exit(1)
if not all(isinstance(item, str) for item in removed + remaining) or not all(
    isinstance(item, list) and len(item) == 2 and all(isinstance(part, str) for part in item)
    for item in failed
):
    print(f"{label}: invalid report: list fields carry the wrong element types")
    sys.exit(1)
detail = (
    f"failed={failed} remaining={remaining} verification_completed={verification_completed} "
    f"enumeration_error={report['enumeration_error']!r} "
    f"verification_error={report['verification_error']!r}"
)
if rc != "0":
    print(f"{label}: cleaner exited {rc} on host={host}: {detail}")
    sys.exit(1)
clean = (
    not failed
    and not remaining
    and verification_completed
    and report["enumeration_error"] is None
)
if clean:
    print(f"{label}: verified clean host={host} removed={len(removed)}")
    sys.exit(0)
print(f"{label}: UNCLEAN host={host}: {detail}")
sys.exit(1)
PY

# judge_cleanup_report LABEL RC < raw-cleaner-stdout ; prints the verdict, returns 0 only when verified clean
judge_cleanup_report() {
    local label="$1" rc="$2" verdict
    if verdict="$(python3 -c "$CLEANUP_REPORT_PARSER" "$label" "$rc")"; then
        echo "  $verdict"
        return 0
    fi
    echo "  ERROR: $verdict" >&2
    return 1
}

# The workstation's own host state, through the same cleaner with the
# repository's paths set explicitly (uv run does not inherit them).
local_host_cleanup() {
    local out rc=0
    local err="$TEARDOWN_TMP/local-cleaner.err"
    out="$(PYTHONPATH=lib:services uv run python -m node_agent.reconcile --clean 2>"$err")" || rc=$?
    if ! judge_cleanup_report "local:$(hostname)" "$rc" <<< "$out"; then
        sed 's/^/    /' "$err" >&2
        return 1
    fi
}

# Namespace presence is established by the lookup's result, never by the
# text of a diagnostic: with --ignore-not-found a successful lookup prints
# the namespace when it exists and nothing when it does not. Any failed
# lookup (an unreachable API, a permission failure, a broken kubeconfig)
# keeps its diagnostic and stops the teardown before any mutation.
NS_LOOKUP_ERR="$TEARDOWN_TMP/namespace-lookup.err"
if ! NS_LOOKUP_OUT="$(kubectl get namespace "$NAMESPACE" --ignore-not-found -o name 2>"$NS_LOOKUP_ERR")"; then
    echo "ERROR: could not determine whether namespace $NAMESPACE exists; nothing was touched:" >&2
    sed 's/^/    /' "$NS_LOOKUP_ERR" >&2
    echo "Teardown incomplete. Fix the above before deploying." >&2
    exit 1
fi
case "$NS_LOOKUP_OUT" in
    "namespace/$NAMESPACE") NAMESPACE_STATE=present ;;
    "") NAMESPACE_STATE=absent ;;
    *)
        echo "ERROR: unexpected namespace lookup result '$NS_LOOKUP_OUT'; nothing was touched" >&2
        echo "Teardown incomplete. Fix the above before deploying." >&2
        exit 1
        ;;
esac

if [ "$NAMESPACE_STATE" = absent ]; then
    echo "Namespace $NAMESPACE does not exist — nothing to tear down."
    # Still clean cluster-scoped resources and local kernel state.
    kubectl delete crd constellationspecs.nodalarc.io --ignore-not-found 2>/dev/null || true
    kubectl delete clusterrole nodalarc-operator nodalarc-orchestrator-cluster \
        nodalarc-node-agent nodalarc-scheduler --ignore-not-found 2>/dev/null || true
    kubectl delete clusterrolebinding nodalarc-operator nodalarc-orchestrator-cluster \
        nodalarc-node-agent nodalarc-scheduler --ignore-not-found 2>/dev/null || true
    kubectl delete clusterrole,clusterrolebinding \
        -l nodalarc.io/managed-by=helm 2>/dev/null || true
    LOCAL_UNVERIFIED=0
    local_host_cleanup || LOCAL_UNVERIFIED=1
    echo "  Namespace absent: no Node Agent can run the host cleaner, so remote host state is NOT verified here."
    echo "  Check the hosts independently (read-only ip link show on every labelled host) before relying on them."
    if [ "$LOCAL_UNVERIFIED" -ne 0 ]; then
        echo "Teardown incomplete: the local host cleanup did not verify clean." >&2
        exit 1
    fi
    echo "=== Teardown complete (namespace absent; remote host state not verified). ==="
    echo "[teardown] Next: make install && make session, or make nuke for square-one reset."
    exit 0
fi

# Step 1: Delete ConstellationSpec CRs — try graceful first, force-strip
# kopf finalizers if it hangs. The Operator may not be running (crashed,
# image pull failure, post-reboot), so graceful delete can block forever.
echo "[1/8] Deleting ConstellationSpec resources..."
if kubectl get constellationspec -n "$NAMESPACE" --no-headers 2>/dev/null | grep -q .; then
    # Strip kopf finalizers from all CRs so delete doesn't hang
    for CR in $(kubectl get constellationspec -n "$NAMESPACE" -o name 2>/dev/null); do
        kubectl patch "$CR" -n "$NAMESPACE" \
            -p '{"metadata":{"finalizers":[]}}' --type=merge 2>/dev/null || true
    done
    kubectl delete constellationspec --all -n "$NAMESPACE" \
        --ignore-not-found --timeout=30s 2>/dev/null || true
fi

# Step 2: Wait for session pods to terminate. Force-delete stuck pods
# (ImagePullBackOff, CrashLoopBackOff, Unknown) after timeout.
echo "[2/8] Waiting for session pods to terminate..."
TIMEOUT=60
ELAPSED=0
while true; do
    SESSION_PODS=$(kubectl get pods -n "$NAMESPACE" \
        -l nodalarc.io/node-id \
        --no-headers 2>/dev/null | grep -v Terminating || true)
    if [ -z "$SESSION_PODS" ]; then
        break
    fi
    sleep 5; ELAPSED=$((ELAPSED+5))
    if [ "$ELAPSED" -ge "$TIMEOUT" ]; then
        echo "  Session pods still present after ${TIMEOUT}s — force deleting..."
        kubectl delete pods -n "$NAMESPACE" -l nodalarc.io/node-id \
            --force --grace-period=0 2>/dev/null || true
        sleep 5
        break
    fi
done

# Step 3: Clean host-side kernel state on EVERY host that carries the Node
# Agent placement label, through the Node Agent's own cleaner, and judge each
# host by its report. The required host set comes from the label, never from
# whichever agent pods happen to exist, and readiness is not a filter: a
# NotReady node can still hold session devices. This runs BEFORE Helm
# uninstall deletes the DaemonSet pods; if any host is unverified the
# teardown refuses below, so the next run keeps its means of retrying. The
# cleaner is the one implementation: it recognizes every managed name
# through runtime_naming, and its report is the only judgement.
echo "[3/8] Cleaning host-side kernel state via the Node Agent cleaner on every labelled host..."
UNVERIFIED_HOSTS=""
if ! REQUIRED_HOSTS="$(kubectl get nodes -l nodalarc.io/node-agent=true \
        -o custom-columns=NAME:.metadata.name --no-headers 2>/dev/null)"; then
    echo "  ERROR: could not read the Node Agent host inventory (nodes labelled nodalarc.io/node-agent=true)" >&2
    UNVERIFIED_HOSTS="<host inventory unreadable>"
    REQUIRED_HOSTS=""
elif [ -z "$REQUIRED_HOSTS" ]; then
    echo "  ERROR: no node carries the nodalarc.io/node-agent=true label; remote host cleanup cannot be verified" >&2
    UNVERIFIED_HOSTS="<no labelled host>"
fi
if ! AGENT_PODS="$(kubectl get pods -n "$NAMESPACE" -l app=nodalarc-node-agent \
        -o custom-columns=NODE:.spec.nodeName,NAME:.metadata.name --no-headers 2>/dev/null)"; then
    echo "  ERROR: could not list the Node Agent pods" >&2
    AGENT_PODS=""
fi
for HOST in $REQUIRED_HOSTS; do
    # A node name is a DNS name; anything else never reaches a file name or an exec.
    case "$HOST" in
        ""|*[!a-zA-Z0-9.-]*|.*|-*)
            echo "  ERROR: unexpected node name in the host inventory: '$HOST'" >&2
            UNVERIFIED_HOSTS="$UNVERIFIED_HOSTS <unexpected-node-name>"
            continue
            ;;
    esac
    POD_NAME="$(printf '%s\n' "$AGENT_PODS" | awk -v h="$HOST" '$1 == h {print $2; exit}')"
    if [ -z "$POD_NAME" ]; then
        echo "  ERROR: $HOST: no Node Agent pod on this host; its kernel state is unverified" >&2
        UNVERIFIED_HOSTS="$UNVERIFIED_HOSTS $HOST"
        continue
    fi
    echo "  Cleaning $HOST via $POD_NAME..."
    EXEC_ERR="$TEARDOWN_TMP/cleaner-$HOST.err"
    RC=0
    OUT="$(kubectl exec "$POD_NAME" -n "$NAMESPACE" -c node-agent -- \
        python -m node_agent.reconcile --clean 2>"$EXEC_ERR")" || RC=$?
    if ! judge_cleanup_report "$HOST" "$RC" <<< "$OUT"; then
        sed 's/^/    /' "$EXEC_ERR" >&2
        UNVERIFIED_HOSTS="$UNVERIFIED_HOSTS $HOST"
    fi
done
LOCAL_UNVERIFIED=0
local_host_cleanup || LOCAL_UNVERIFIED=1

# Refuse before uninstalling the Node Agents when any host is unverified:
# uninstalling them would take away the next teardown's means of retrying.
if [ "$LOCAL_UNVERIFIED" -ne 0 ]; then
    UNVERIFIED_HOSTS="$UNVERIFIED_HOSTS local:$(hostname)"
fi
if [ -n "$UNVERIFIED_HOSTS" ]; then
    echo "" >&2
    echo "ERROR: host cleanup unverified on:${UNVERIFIED_HOSTS}" >&2
    echo "Refusing to uninstall the Node Agents; the next teardown needs them to retry remote cleanup." >&2
    echo "Teardown incomplete. Fix the above before deploying." >&2
    exit 1
fi

# Step 4: Helm uninstall — removes all Helm-managed resources including DaemonSet
echo "[4/8] Helm uninstall..."
helm uninstall "$HELM_RELEASE_NAME" -n "$NAMESPACE" \
    --ignore-not-found --timeout=120s 2>/dev/null || true

# Step 5: Wait for DaemonSet pod to actually terminate
echo "[5/8] Waiting for Node Agent DaemonSet pod to terminate..."
kubectl wait pod -n "$NAMESPACE" \
    -l app=nodalarc-node-agent \
    --for=delete --timeout=60s 2>/dev/null || true

# Step 6: Delete namespace — strip finalizers if stuck
echo "[6/8] Deleting namespace..."
kubectl delete namespace "$NAMESPACE" --timeout=30s 2>/dev/null || true
# If still stuck (Terminating), force-remove finalizers
if kubectl get namespace "$NAMESPACE" 2>/dev/null | grep -q Terminating; then
    echo "  Namespace stuck in Terminating — removing finalizers..."
    kubectl get namespace "$NAMESPACE" -o json 2>/dev/null | \
        python3 -c "import sys,json; ns=json.load(sys.stdin); ns['spec']['finalizers']=[]; print(json.dumps(ns))" | \
        kubectl replace --raw "/api/v1/namespaces/$NAMESPACE/finalize" -f - 2>/dev/null || true
    sleep 3
fi

# Step 7: Delete cluster-scoped resources
echo "[7/8] Deleting cluster-scoped resources..."
# CRD may also be stuck due to finalizers on orphaned instances
kubectl patch crd constellationspecs.nodalarc.io \
    -p '{"metadata":{"finalizers":[]}}' --type=merge 2>/dev/null || true
kubectl delete crd constellationspecs.nodalarc.io \
    --ignore-not-found --timeout=10s 2>/dev/null || true
kubectl delete clusterrole \
    nodalarc-operator nodalarc-orchestrator-cluster \
    nodalarc-node-agent nodalarc-scheduler \
    --ignore-not-found 2>/dev/null || true
kubectl delete clusterrolebinding \
    nodalarc-operator nodalarc-orchestrator-cluster \
    nodalarc-node-agent nodalarc-scheduler \
    --ignore-not-found 2>/dev/null || true
# Label-based catch-all
kubectl delete clusterrole,clusterrolebinding \
    -l nodalarc.io/managed-by=helm 2>/dev/null || true

# Step 8: Verify — nothing should remain. Host kernel state on every
# labelled host and on this workstation was verified absent by the Node
# Agent cleaner's reports in step 3, before uninstall; the checks here cover
# the cluster objects.
echo "[8/8] Verifying clean state..."
ERRORS=0

# Check no nodalarc pods survive
PODS=$(kubectl get pods -A 2>/dev/null | grep nodalarc | grep -v Terminating || true)
if [ -n "$PODS" ]; then
    echo "ERROR: Nodalarc pods still running:"
    echo "$PODS"
    ERRORS=$((ERRORS+1))
fi

# Check namespace gone
if kubectl get namespace "$NAMESPACE" 2>/dev/null | grep -q "$NAMESPACE"; then
    echo "ERROR: Namespace $NAMESPACE still exists"
    ERRORS=$((ERRORS+1))
fi

# Check no nodalarc CRD survives
if kubectl get crd constellationspecs.nodalarc.io &>/dev/null 2>&1; then
    echo "ERROR: ConstellationSpec CRD still exists"
    ERRORS=$((ERRORS+1))
fi

if [ "$ERRORS" -gt 0 ]; then
    echo ""
    echo "Teardown incomplete. Fix the above before deploying."
    exit 1
fi

echo ""
echo "=== Teardown complete. Cluster is clean. ==="
echo "[teardown] Next: make install && make session, or make nuke for square-one reset."
