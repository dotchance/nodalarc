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

printf '%s\n' "$output_dir"
