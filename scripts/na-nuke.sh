#!/usr/bin/env bash
# Best-effort square-one reset: leave K3s and the repo, remove NodalArc state.

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-nodalarc}"
SUDO_CTR="${SUDO_CTR:-sudo}"
if [ -n "$SUDO_CTR" ]; then
    read -r -a SUDO_CTR_CMD <<< "$SUDO_CTR"
else
    SUDO_CTR_CMD=()
fi

failures=0
summary=()

run_phase() {
    local name="$1"
    shift
    echo ""
    echo "[nuke] $name"
    if "$@"; then
        summary+=("$name: ok")
    else
        rc=$?
        summary+=("$name: failed ($rc)")
        failures=$((failures + 1))
    fi
}

clean_artifacts() {
    rm -rf "$ROOT_DIR/frontend/dist" "$ROOT_DIR/nodalpath/console/frontend/dist"
    find "$ROOT_DIR" -type d -name __pycache__ ! -path "$ROOT_DIR/.venv/*" -exec rm -rf {} + 2>/dev/null || true
    rm -rf "$ROOT_DIR/.pytest_cache" "$ROOT_DIR/.ruff_cache"
}

clean_deps() {
    rm -rf "$ROOT_DIR/.venv" "$ROOT_DIR/lib/nodalarc.egg-info"
    rm -rf "$ROOT_DIR/frontend/node_modules" "$ROOT_DIR/nodalpath/console/frontend/node_modules"
}

clean_docker_images() {
    bash "$ROOT_DIR/scripts/na-clean-images.sh"
}

verify_square_one() {
    local errors=0
    if kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
        echo "[nuke] ERROR: namespace still exists: $NAMESPACE" >&2
        errors=$((errors + 1))
    fi
    if kubectl get pods -A 2>/dev/null | grep -q nodalarc; then
        echo "[nuke] ERROR: NodalArc pods still exist" >&2
        errors=$((errors + 1))
    fi
    if "${SUDO_CTR_CMD[@]}" k3s ctr images ls -q 2>/dev/null | grep -qE '(^|/)nodalarc/'; then
        echo "[nuke] ERROR: NodalArc images still exist in local K3s containerd" >&2
        errors=$((errors + 1))
    fi
    if ! docker_output="$(docker images --format '{{.Repository}}:{{.Tag}}' 2>&1)"; then
        echo "[nuke] ERROR: cannot verify local Docker images" >&2
        echo "$docker_output" >&2
        errors=$((errors + 1))
    elif printf '%s\n' "$docker_output" | grep -qE '(^|/)nodalarc/'; then
        echo "[nuke] ERROR: local Docker still has NodalArc images" >&2
        errors=$((errors + 1))
    fi
    for path in "$ROOT_DIR/frontend/dist" "$ROOT_DIR/nodalpath/console/frontend/dist" "$ROOT_DIR/.venv" "$ROOT_DIR/frontend/node_modules"; do
        if [ -e "$path" ]; then
            echo "[nuke] ERROR: generated/dependency path still exists: ${path#$ROOT_DIR/}" >&2
            errors=$((errors + 1))
        fi
    done
    return "$errors"
}

run_phase "registry-delete" bash "$ROOT_DIR/scripts/clean-registry.sh"
run_phase "remote-containerd-purge" env PURGE_SCOPE=remote REMOTE_REQUIRED=auto bash "$ROOT_DIR/scripts/na-purge-containerd.sh"
run_phase "teardown" bash "$ROOT_DIR/scripts/na-teardown.sh"
run_phase "local-containerd-purge" env PURGE_SCOPE=local REMOTE_REQUIRED=0 bash "$ROOT_DIR/scripts/na-purge-containerd.sh"
run_phase "local-docker-image-clean" clean_docker_images
run_phase "build-artifact-clean" clean_artifacts
run_phase "dependency-clean" clean_deps
run_phase "verification" verify_square_one

echo ""
echo "[nuke] Summary:"
for item in "${summary[@]}"; do
    echo "  $item"
done

if [ "$failures" -ne 0 ]; then
    echo "[nuke] Incomplete: $failures required phase(s) failed." >&2
    exit 1
fi

echo "[nuke] Complete. K3s and repo remain; NodalArc runtime state is removed."
echo "[nuke] Next: make all"
