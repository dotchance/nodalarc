#!/usr/bin/env bash
# Remove local Docker images built by NodalArc.

set -euo pipefail

command -v docker >/dev/null 2>&1 || {
    echo "[clean-images] ERROR: docker not found" >&2
    exit 1
}

images="$(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '(^|/)nodalarc/' || true)"
if [ -z "$images" ]; then
    echo "[clean-images] No local NodalArc Docker images found."
else
    printf '%s\n' "$images" | xargs -r docker rmi -f
fi

docker builder prune -af >/dev/null 2>&1 || true
echo "[clean-images] Docker images removed."
