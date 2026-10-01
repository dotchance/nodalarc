#!/usr/bin/env bash
# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
#
# Lists what each built image carries under its application directories and
# refuses a file an image does not need to run: documentation, build files,
# Python caches, interface sources, source maps and frontend sources.
#
# usage: na-image-contents.sh [IMAGE...]
#
# With no argument the images are the built images of the inventory
# (scripts/na-images.sh list-build-images) at the tree's tag, under the same
# MODE and REGISTRY_HOST the build used. Nothing in an image is executed: the
# listing is read from an export of a created, never started, container.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# The directories that hold NodalArc's own files, as paths inside an export.
APP_ROOTS='^(app|usr/share/nginx/html)/'
# The one document an image serves: the VS-API hands the grammar reference to
# the session builder.
ALLOWED_DOCS='^app/docs/ops/configuration-grammar\.md$'
REFUSED='(^|/)(README[^/]*|Dockerfile)$|(^|/)(__pycache__|tests|\.git)(/|$)|\.(md|pyc|proto|map|ts|tsx)$'

if [ "$#" -gt 0 ]; then
    images=("$@")
else
    TAG="${TAG:-$(bash "$ROOT_DIR/scripts/na-tag.sh")}"
    if ! listing="$(TAG="$TAG" NA_IMAGES_NO_CLUSTER=1 bash "$ROOT_DIR/scripts/na-images.sh" list-build-images)"; then
        echo "[contents] ERROR: could not list the built images" >&2
        exit 2
    fi
    mapfile -t images < <(printf '%s\n' "$listing" | cut -f4)
fi
if [ "${#images[@]}" -eq 0 ]; then
    echo "[contents] ERROR: no image to inspect" >&2
    exit 2
fi

failed=0
for image in "${images[@]}"; do
    if ! container="$(docker create "$image" 2>&1)"; then
        echo "[contents] ERROR: $image: $container" >&2
        exit 2
    fi
    if ! files="$(docker export "$container" | tar -t | grep -E "$APP_ROOTS" | grep -v '/$' || true)"; then
        docker rm "$container" >/dev/null
        echo "[contents] ERROR: $image: could not list the image" >&2
        exit 2
    fi
    docker rm "$container" >/dev/null
    count=0
    [ -n "$files" ] && count="$(printf '%s\n' "$files" | wc -l)"
    refused="$(printf '%s\n' "$files" | grep -E "$REFUSED" | grep -Ev "$ALLOWED_DOCS" || true)"
    if [ -n "$refused" ]; then
        failed=1
        echo "[contents] REFUSED $image: $count application files, of which these are not needed to run:"
        printf '%s\n' "$refused" | sed 's/^/    /'
    else
        echo "[contents] ok      $image: $count application files"
    fi
done

if [ "$failed" -ne 0 ]; then
    echo "[contents] ERROR: an image carries files it does not need to run" >&2
    exit 1
fi
