#!/usr/bin/env bash
# clean-registry.sh — Remote purge of nodalarc/* images from REGISTRY_HOST
# via the Registry V2 HTTP API.
#
# Works against any OCI-compliant registry: CNCF distribution, Harbor,
# GHCR, GCR, ECR, Quay, Artifactory. No SSH, no docker daemon on the
# registry host, no assumptions about how the registry is deployed
# (container, systemd service, managed cloud endpoint).
#
# Requirements:
#   * crane      — single Go binary from github.com/google/go-containerregistry
#                  install: go install github.com/google/go-containerregistry/cmd/crane@latest
#                  or: download release from
#                       https://github.com/google/go-containerregistry/releases
#   * Registry-side: delete enabled (distribution: delete.enabled=true in
#                    config.yml, or REGISTRY_STORAGE_DELETE_ENABLED=true env).
#                    Modern managed registries have this on by default.
#
# Behavior:
#   * REGISTRY_HOST empty  → single-node mode, no external registry, exits 0.
#   * REGISTRY_HOST set    → deletes every tag under nodalarc/* via crane.
#
# HTTP vs HTTPS: probes the registry once. If it responds on plain HTTP,
# crane is invoked with --insecure (dev clusters with a local registry).
# If only HTTPS responds, crane uses default TLS verification (prod).
# Override with REGISTRY_INSECURE={1|0} to force one or the other.
#
# Storage reclaim: this script does NOT reclaim underlying blob storage
# — that requires a registry-side garbage-collect step (e.g. distribution's
# `docker-registry garbage-collect`). Run it on a systemd timer on the
# registry host, or use a managed registry that does it continuously.

set -euo pipefail

host="${REGISTRY_HOST:-}"

if [ -z "$host" ]; then
    echo "[clean-registry] REGISTRY_HOST empty — single-node mode, no external registry to clean."
    exit 0
fi

if ! command -v crane >/dev/null 2>&1; then
    cat >&2 <<'EOF'
ERROR: crane not found. Install one of:
  go install github.com/google/go-containerregistry/cmd/crane@latest
  (or download a release binary from
   https://github.com/google/go-containerregistry/releases)
EOF
    exit 1
fi

# Decide HTTP vs HTTPS.
crane_opts=""
case "${REGISTRY_INSECURE:-auto}" in
    1|true|yes)
        crane_opts="--insecure"
        ;;
    0|false|no|"")
        crane_opts=""
        ;;
    auto)
        if curl -sf -o /dev/null --connect-timeout 3 "http://$host/v2/" 2>/dev/null; then
            crane_opts="--insecure"
        fi
        ;;
esac

echo "[clean-registry] Purging nodalarc/* from $host ${crane_opts:+(insecure)}..."
if ! catalog=$(crane catalog "$host" $crane_opts 2>&1); then
    echo "[clean-registry] ERROR: registry catalog failed for $host" >&2
    echo "$catalog" >&2
    exit 1
fi
repos=$(printf '%s\n' "$catalog" | grep '^nodalarc/' || true)
if [ -z "$repos" ]; then
    echo "[clean-registry]   (no nodalarc/* repos at $host)"
    exit 0
fi

failed=0
for repo in $repos; do
    if ! tags=$(crane ls "$host/$repo" $crane_opts 2>&1); then
        echo "  FAILED  $host/$repo  (could not list tags)" >&2
        echo "$tags" >&2
        failed=1
        continue
    fi
    for tag in $tags; do
        # Distribution registry DELETE requires the server-authoritative
        # digest — deleting by tag returns DIGEST_INVALID. Resolve the
        # digest via `crane digest` first, then DELETE by digest.
        digest=$(crane digest "$host/$repo:$tag" $crane_opts 2>/dev/null || true)
        if [ -z "$digest" ]; then
            echo "  SKIPPED $host/$repo:$tag  (could not resolve digest)" >&2
            continue
        fi
        if crane delete "$host/$repo@$digest" $crane_opts 2>/dev/null; then
            echo "  deleted $host/$repo:$tag  ($digest)"
        else
            echo "  FAILED  $host/$repo:$tag  (registry may not have delete enabled)" >&2
            failed=1
        fi
    done
done

if [ "$failed" -eq 1 ]; then
    cat >&2 <<'EOF'

Some deletions failed. For CNCF distribution registry, verify:
  - config.yml contains:   delete: { enabled: true }
  - or the process has:    REGISTRY_STORAGE_DELETE_ENABLED=true
  - then:                   systemctl restart docker-registry
EOF
    exit 1
fi

echo "[clean-registry] Done. Storage reclaim (blob GC) is a registry-side task."
