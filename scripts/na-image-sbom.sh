#!/usr/bin/env bash
# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
#
# Adds an SBOM to a built image.
#
# usage: na-image-sbom.sh COMPONENT IMAGE [TAG...]
#
# Syft scans every layer of IMAGE, so a package that an early layer holds and
# a later layer replaced or removed is listed too. The result is an SPDX
# document that names nodalarc-COMPONENT at the version IMAGE's own label
# states. A last layer puts the SPDX document at /nodalarc/sbom.spdx.json, and
# the finished image takes IMAGE's reference and each TAG.
#
# Syft runs in its own container, pinned by digest, and reads an export of
# IMAGE. It is never part of an image.
set -euo pipefail

SYFT_IMAGE='ghcr.io/anchore/syft:v1.52.0@sha256:500e2d872ac019436926e8322b4fc1f39441d94d21f6f4046c6ff29b30e8cb02'
SBOM_PATH='/nodalarc/sbom.spdx.json'

if [ "$#" -lt 2 ]; then
    echo "usage: na-image-sbom.sh COMPONENT IMAGE [TAG...]" >&2
    exit 2
fi
component="$1"
image="$2"
shift 2

if ! version="$(docker inspect --format '{{index .Config.Labels "org.opencontainers.image.version"}}' "$image")"; then
    echo "[sbom] ERROR: $image is not a local image" >&2
    exit 2
fi
if [ -z "$version" ]; then
    echo "[sbom] ERROR: $image carries no org.opencontainers.image.version label" >&2
    exit 2
fi

work="$(mktemp -d)"
if [ -z "$work" ] || [ ! -d "$work" ]; then
    echo "[sbom] ERROR: could not create a work directory" >&2
    exit 2
fi
trap 'rm -rf -- "$work"' EXIT

docker save "$image" -o "$work/image.tar"
# The lockfile cataloger is added for the frontend image, which carries the
# lockfile of the bundle it serves; development-only packages stay out.
docker run --rm \
    -e SYFT_CHECK_FOR_APP_UPDATE=false \
    -e SYFT_JAVASCRIPT_INCLUDE_DEV_DEPENDENCIES=false \
    -v "$work:/work" \
    "$SYFT_IMAGE" scan docker-archive:/work/image.tar \
    --scope all-layers \
    --select-catalogers +javascript-lock-cataloger \
    --source-name "nodalarc-$component" \
    --source-version "$version" \
    -o "spdx-json=/work/sbom.spdx.json" \
    --quiet
if [ ! -s "$work/sbom.spdx.json" ]; then
    echo "[sbom] ERROR: the scan of $image wrote no document" >&2
    exit 1
fi
rm -f -- "$work/image.tar"

printf 'FROM %s\nCOPY sbom.spdx.json %s\n' "$image" "$SBOM_PATH" > "$work/Dockerfile"
tags=(-t "$image")
for tag in "$@"; do
    tags+=(-t "$tag")
done
docker build --quiet "${tags[@]}" "$work" >/dev/null
echo "[sbom] $image: nodalarc-$component $version, every layer scanned"
