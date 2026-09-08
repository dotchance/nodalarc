#!/usr/bin/env bash
# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
#
# Render the Helm chart into build/ with PROJECT_VERSION injected into
# Chart.yaml. The source chart intentionally keeps no release version literal.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_CHART="${1:-deploy/helm}"
OUTPUT_CHART="${2:-build/helm/nodalarc}"

source_dir="$SOURCE_CHART"
if [[ "$source_dir" != /* ]]; then
    source_dir="$ROOT_DIR/$source_dir"
fi

output_dir="$OUTPUT_CHART"
if [[ "$output_dir" != /* ]]; then
    output_dir="$ROOT_DIR/$output_dir"
fi

template="$source_dir/Chart.yaml.in"
if [[ ! -f "$template" ]]; then
    echo "[helm-chart] ERROR: missing chart template: $template" >&2
    exit 2
fi

project_version="${PROJECT_VERSION:-}"
if [[ -z "$project_version" ]]; then
    project_version="$(bash "$ROOT_DIR/scripts/na-project-version.sh")"
fi

rm -rf "$output_dir"
mkdir -p "$output_dir"

(
    cd "$source_dir"
    tar --exclude='./Chart.yaml' --exclude='./Chart.yaml.in' -cf - .
) | (
    cd "$output_dir"
    tar -xf -
)

escaped_version="${project_version//\\/\\\\}"
escaped_version="${escaped_version//&/\\&}"
escaped_version="${escaped_version//|/\\|}"
sed "s|@PROJECT_VERSION@|$escaped_version|g" "$template" > "$output_dir/Chart.yaml"

# The platform configuration is authored once, in configs/platform.yaml;
# the chart copy is rendered here with the namespace templated, so the
# source chart never carries a second copy.
platform_copy="$output_dir/files/platform.yaml"
mkdir -p "$output_dir/files"
(cd "$ROOT_DIR" && PYTHONPATH=lib uv run --quiet python -m nodalarc.platform_config --render-chart-copy configs/platform.yaml) > "$platform_copy"
if [[ ! -s "$platform_copy" ]]; then
    echo "[helm-chart] ERROR: the platform configuration copy rendered empty" >&2
    exit 2
fi

# The NATS messaging inventory (deployed streams and authorization patterns)
# is authored once, in lib/nodalarc/nats_channels.py, and rendered here into
# the assembled chart; the source chart never carries a copy.
mkdir -p "$output_dir/files"
messaging="$output_dir/files/nats-messaging.yaml"
(cd "$ROOT_DIR" && PYTHONPATH=lib uv run --quiet python -m nodalarc.nats_channels --render-messaging) > "$messaging"
if [[ ! -s "$messaging" ]]; then
    echo "[helm-chart] ERROR: the NATS messaging inventory rendered empty" >&2
    exit 2
fi

# The chart's identity: a digest of the content the release will carry
# (templates, files, CRDs, default values), recorded in Chart.yaml so a
# later single-service deploy can prove the release runs this chart.
if grep -q '^annotations:' "$output_dir/Chart.yaml"; then
    echo "[helm-chart] ERROR: Chart.yaml.in must not declare annotations; the assembler records them" >&2
    exit 2
fi
chart_digest="$(cd "$ROOT_DIR" && uv run --quiet python scripts/na-chart-identity.py digest "$output_dir")"
if [[ -z "$chart_digest" ]]; then
    echo "[helm-chart] ERROR: the chart digest rendered empty" >&2
    exit 2
fi
printf 'annotations:\n  nodalarc.io/chart-digest: "%s"\n' "$chart_digest" >> "$output_dir/Chart.yaml"

printf '%s\n' "$output_dir"
