#!/usr/bin/env bash
# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
#
# Build and publish a NodalArc release: the published images and the Helm chart
# that installs them. A release is a list of component versions and image
# digests; this script produces that list and the chart from it.
#
#   na-release.sh images REGISTRY RECORD
#       Build every release image from a clean checkout (make build, the same
#       build the development path runs), push each one as
#       REGISTRY/nodalarc/<component>:<version, + written as _>, copy the
#       third-party images there by digest, and write RECORD (JSON): each
#       component's version and its image pinned by digest.
#
#   na-release.sh chart RELEASE RECORD OUT [CHART_REPOSITORY]
#       Write the chart for release RELEASE from RECORD: every image value, the
#       workload image map and the release number set from the record. Package
#       it into OUT with the release list OUT/RELEASE.yaml, and push it to
#       CHART_REPOSITORY (oci://...) when one is given. Building no image, it
#       turns a qualified candidate's record into the final release unchanged.
#
# PLAIN_HTTP=1 talks to a registry over plain HTTP (a test registry).
# REGISTRY is a registry and namespace, for example ghcr.io/<owner>.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# The images a release publishes: the session images the shipped profiles name
# through the workload image map, and the platform services. The probe and
# measurement images are not part of a release.
SESSION_IMAGES=(base frr hdtn ion picoquic ud3tn-tools)
PLATFORM_IMAGES=(ome scheduler node-agent vs-api operator vf)
# Components with a version and no image of their own.
OTHER_COMPONENTS=(chart catalog grammar)

crane_flags=()
helm_flags=()
if [ "${PLAIN_HTTP:-0}" = "1" ]; then
    crane_flags=(--insecure)
    helm_flags=(--plain-http)
fi

fail() {
    echo "[release] ERROR: $*" >&2
    exit 1
}

require() {
    local tool
    for tool in "$@"; do
        command -v "$tool" >/dev/null 2>&1 || fail "$tool is not installed"
    done
}

semver_tag() {
    # An image tag may not contain +; SemVer build metadata is written with _,
    # the rule Helm applies to chart versions in OCI registries.
    printf '%s\n' "${1//+/_}"
}

release_images() {
    local registry="$1" record="$2" work="$3"
    require docker crane git make
    if [ -n "$(git status --porcelain)" ]; then
        fail "the checkout has uncommitted changes; a release is built from a commit"
    fi
    local tag
    tag="$(bash scripts/na-tag.sh)"
    [ "$tag" = "$(git rev-parse --short HEAD)" ] || fail "content tag $tag is not the commit"

    echo "[release] Building every image at $tag..."
    make build REGISTRY_HOST= TAG="$tag"

    local name version local_ref remote digest
    : > "$work/components.tsv"
    for name in "${SESSION_IMAGES[@]}" "${PLATFORM_IMAGES[@]}"; do
        version="$(TAG="$tag" bash scripts/na-images.sh component-version "$name")"
        local_ref="$(MODE=single-node REGISTRY_HOST= TAG="$tag" NA_IMAGES_NO_CLUSTER=1 \
            bash scripts/na-images.sh image-for "$name")"
        remote="$registry/nodalarc/$name:$(semver_tag "$version")"
        echo "[release] $name $version -> $remote"
        docker tag "$local_ref" "$remote"
        docker push "$remote" >/dev/null
        digest="$(crane digest "${crane_flags[@]}" "$remote")"
        [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || fail "$remote has no digest in the registry"
        printf '%s\t%s\t%s@%s\n' "$name" "$version" "$remote" "$digest" >> "$work/components.tsv"
    done
    for name in "${OTHER_COMPONENTS[@]}"; do
        version="$(TAG="$tag" bash scripts/na-images.sh component-version "$name")"
        printf '%s\t%s\t-\n' "$name" "$version" >> "$work/components.tsv"
    done

    # The third-party runtime images, copied by digest so an install pulls
    # every image from one registry. A copy keeps the upstream digest.
    local upstream repo_tag
    : > "$work/third_party.tsv"
    for name in nats nats-box; do
        upstream="$(NA_IMAGES_NO_CLUSTER=1 bash scripts/na-images.sh image-for "$name")"
        repo_tag="${upstream%@*}"
        remote="$registry/nodalarc/${repo_tag##*/}"
        echo "[release] $name $upstream -> $remote"
        crane copy "${crane_flags[@]}" "$upstream" "$remote" >/dev/null
        digest="$(crane digest "${crane_flags[@]}" "$remote")"
        [ "$digest" = "${upstream##*@}" ] || fail "the copy of $upstream has digest $digest"
        printf '%s\t%s@%s\n' "$name" "$remote" "$digest" >> "$work/third_party.tsv"
    done

    python3 - "$work/components.tsv" "$work/third_party.tsv" "$(git rev-parse HEAD)" "$record" \
        "${SESSION_IMAGES[*]}" <<'PY'
import json
import sys

components_tsv, third_party_tsv, commit, record_path, session = sys.argv[1:]
components = {}
for line in open(components_tsv):
    name, version, image = line.rstrip("\n").split("\t")
    components[name] = {"version": version} | ({"image": image} if image != "-" else {})
third_party = {}
for line in open(third_party_tsv):
    name, image = line.rstrip("\n").split("\t")
    third_party[name] = {"image": image}
record = {
    "commit": commit,
    "components": components,
    "third_party": third_party,
    "session_images": session.split(),
}
with open(record_path, "w") as handle:
    json.dump(record, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY
    echo "[release] Record written: $record"
}

release_chart() {
    local release="$1" record="$2" out="$3" repository="${4:-}" work="$5"
    require helm uv python3
    [[ "$release" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]] \
        || fail "release '$release' is not a SemVer version without build metadata"
    [ -s "$record" ] || fail "record $record is missing"
    mkdir -p "$out"

    python3 - "$record" "$release" "$work/record.json" <<'PY'
import json
import sys

record_path, release, output = sys.argv[1:]
record = json.load(open(record_path))
record["release"] = release
json.dump(record, open(output, "w"), indent=2, sort_keys=True)
PY
    uv run --quiet python scripts/na-release-values.py deploy/helm/values.yaml \
        "$work/record.json" "$work/values.yaml"
    local chart
    chart="$(RELEASE_VALUES="$work/values.yaml" PROJECT_VERSION="$release" \
        bash scripts/na-render-helm-chart.sh deploy/helm "$work/chart/nodalarc")"
    helm lint "$chart" >/dev/null
    helm package "$chart" -d "$out" >/dev/null
    local package="$out/nodalarc-$release.tgz"
    [ -s "$package" ] || fail "helm package wrote no $package"

    python3 - "$work/record.json" "$out/$release.yaml" "$repository" <<'PY'
import json
import sys

record_path, list_path, repository = sys.argv[1:]
record = json.load(open(record_path))
release = record["release"]
lines = [
    f"# NodalArc release {release}: the version of every component it consists of,",
    "# and the image each one runs, pinned by digest.",
    f"release: {release}",
    f"commit: {record['commit']}",
]
if repository:
    lines.append(f"chart: {repository}/nodalarc:{release}")
lines.append("components:")
for name, component in sorted(record["components"].items()):
    lines.append(f"  {name}:")
    lines.append(f"    version: {component['version']}")
    if "image" in component:
        lines.append(f"    image: {component['image']}")
lines.append("third_party:")
for name, component in sorted(record["third_party"].items()):
    lines.append(f"  {name}:")
    lines.append(f"    image: {component['image']}")
open(list_path, "w").write("\n".join(lines) + "\n")
PY

    if [ -n "$repository" ]; then
        echo "[release] Pushing the chart to $repository..."
        helm push "${helm_flags[@]}" "$package" "$repository"
    fi
    echo "[release] Chart: $package"
    echo "[release] Release list: $out/$release.yaml"
}

work="$(mktemp -d)"
if [ -z "$work" ] || [ ! -d "$work" ]; then
    fail "could not create a working directory"
fi
trap 'rm -rf -- "$work"' EXIT

case "${1:-}" in
    images)
        [ $# -eq 3 ] || fail "usage: na-release.sh images REGISTRY RECORD"
        release_images "$2" "$3" "$work"
        ;;
    chart)
        [ $# -eq 4 ] || [ $# -eq 5 ] || fail "usage: na-release.sh chart RELEASE RECORD OUT [CHART_REPOSITORY]"
        release_chart "$2" "$3" "$4" "${5:-}" "$work"
        ;;
    *)
        sed -n '/^# Build and publish/,/^# REGISTRY is/p' "$0" >&2
        exit 2
        ;;
esac
