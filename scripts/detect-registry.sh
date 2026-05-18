#!/usr/bin/env bash
# detect-registry.sh — Best-effort discovery of the K3s container registry
# hostname on this build host.
#
# Output: hostname:port (e.g. "node01:5000") on success; empty on any failure.
# Exit code is always 0 — callers treat empty output as "single-node mode".
#
# Source of truth: /etc/rancher/k3s/registries.yaml on the build host. That
# file is written by the cluster operator and already declares which
# hostname the K3s containerd will pull images from. We read it instead of
# inventing a second source of truth.
#
# Override: set REGISTRY_HOST in the environment (or config.mk) to skip
# auto-detection entirely. Set REGISTRIES_YAML to point at a non-default
# path for testing.

set -u

REGISTRIES_YAML="${REGISTRIES_YAML:-/etc/rancher/k3s/registries.yaml}"

if [ ! -r "$REGISTRIES_YAML" ]; then
    exit 0
fi

# Prefer yq if available (handles quoting, nesting, edge cases correctly).
if command -v yq >/dev/null 2>&1; then
    host="$(yq eval '.mirrors | keys | .[0]' "$REGISTRIES_YAML" 2>/dev/null || true)"
    case "$host" in
        ""|null) ;;
        *) printf '%s\n' "$host"; exit 0 ;;
    esac
fi

# Fallback: awk parser. Finds the first quoted key indented under `mirrors:`.
# Matches shapes like:
#   mirrors:
#     "node01:5000":
#       endpoint:
#         - "http://node01:5000"
awk '
    /^mirrors:[[:space:]]*$/       { in_mirrors = 1; next }
    /^[^[:space:]]/                { in_mirrors = 0 }
    in_mirrors && /^[[:space:]]+"/ {
        line = $0
        sub(/^[[:space:]]+"/, "", line)
        sub(/":.*$/, "",          line)
        if (line != "") { print line; exit }
    }
' "$REGISTRIES_YAML" || true
