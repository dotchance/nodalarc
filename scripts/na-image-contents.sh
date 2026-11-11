#!/usr/bin/env bash
# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
#
# Lists what each built image carries under its application directories and
# refuses a file an image does not need to run: documentation, build files,
# Python caches, interface sources, source maps and frontend sources. It also
# requires each image's SBOM: an SPDX document that names the component at the
# version the image's own label states and lists at least one package. An
# image whose license label names AGPL-3.0-only must carry the tree's LICENSE,
# NOTICE, THIRD_PARTY_NOTICES.md and every license text in LICENSES/. A page
# image (it serves index.html) must also serve the tree's LICENSE, NOTICE and
# THIRD_PARTY_NOTICES.md and the license of each bundled npm package under
# legal/, and its label must name every license those packages use. An image
# that carries the scanner is refused.
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
REFUSED='(^|/)(README[^/]*|Dockerfile|VERSION)$|(^|/)(__pycache__|tests|\.git)(/|$)|\.(md|pyc|proto|map|ts|tsx)$'
# Where every image keeps its SBOM, and the build tool that must not ship.
SBOM_PATH='nodalarc/sbom.spdx.json'
LEGAL_DIR='usr/share/doc/nodalarc'
mapfile -t LEGAL_FILES < <(cd "$ROOT_DIR" && printf '%s\n' LICENSE NOTICE THIRD_PARTY_NOTICES.md && find LICENSES -type f | sort)
PAGE_INDEX='usr/share/nginx/html/index.html'
SERVED_LEGAL_DIR='usr/share/nginx/html/legal'
SERVED_LEGAL_FILES=(LICENSE:LICENSE.txt NOTICE:NOTICE.txt THIRD_PARTY_NOTICES.md:THIRD_PARTY_NOTICES.txt)
SCANNER='(^|/)syft$'

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
    if ! listing="$(docker export "$container" | tar -t)"; then
        docker rm "$container" >/dev/null
        echo "[contents] ERROR: $image: could not list the image" >&2
        exit 2
    fi
    files="$(printf '%s\n' "$listing" | grep -E "$APP_ROOTS" | grep -v '/$' || true)"
    scanner="$(printf '%s\n' "$listing" | grep -E "$SCANNER" || true)"
    # The component is the last path element of the reference, without its
    # tag or digest: registry.example:5000/nodalarc/ome@sha256:... -> ome. The path is
    # cut first, since a registry address can hold a colon of its own.
    component="${image##*/}"; component="${component%%@*}"; component="${component%%:*}"
    labelled="$(docker inspect --format '{{index .Config.Labels "org.opencontainers.image.version"}}' "$image")"
    # docker cp reads the one file; a failed copy means the image has none.
    sbom="absent"
    if document="$(docker cp "$container:/$SBOM_PATH" - 2>/dev/null | tar -xO)" && [ -n "$document" ]; then
        sbom="$(python3 -c '
import json, sys
doc = json.load(sys.stdin)
roots = {r["relatedSpdxElement"] for r in doc.get("relationships", []) if r.get("relationshipType") == "DESCRIBES"}
subject = [p for p in doc.get("packages", []) if p["SPDXID"] in roots]
if len(subject) != 1:
    sys.exit("the document does not describe exactly one subject")
fields = (doc.get("spdxVersion"), subject[0].get("name"), subject[0].get("versionInfo"))
if not all(isinstance(f, str) and f and " " not in f for f in fields):
    sys.exit("the document lacks an SPDX version, a subject name or a subject version")
print(*fields, len(doc.get("packages", [])) - 1)
' <<< "$document")" || sbom="unreadable"
    fi
    licenses="$(docker inspect --format '{{index .Config.Labels "org.opencontainers.image.licenses"}}' "$image")"
    legal=()
    notices="not required by its license label"
    if [[ " $licenses " == *" AGPL-3.0-only "* ]]; then
        notices="carried"
        for name in "${LEGAL_FILES[@]}"; do
            if ! carried="$(docker cp "$container:/$LEGAL_DIR/$name" - 2>/dev/null | tar -xO | sha256sum)"; then
                legal+=("no /$LEGAL_DIR/$name")
            elif [ "$carried" != "$(sha256sum < "$ROOT_DIR/$name")" ]; then
                legal+=("/$LEGAL_DIR/$name differs from the tree's $name")
            fi
        done
    fi
    if printf '%s\n' "$listing" | grep -qx "$PAGE_INDEX"; then
        notices="$notices; page serves legal/"
        for pair in "${SERVED_LEGAL_FILES[@]}"; do
            name="${pair%%:*}"
            served="$SERVED_LEGAL_DIR/${pair#*:}"
            if ! carried="$(docker cp "$container:/$served" - 2>/dev/null | tar -xO | sha256sum)"; then
                legal+=("no /$served")
            elif [ "$carried" != "$(sha256sum < "$ROOT_DIR/$name")" ]; then
                legal+=("/$served differs from the tree's $name")
            fi
        done
        if ! bundled="$(docker cp "$container:/$SERVED_LEGAL_DIR/third-party-licenses.txt" - 2>/dev/null | tar -xO)" \
            || [ -z "$bundled" ]; then
            legal+=("no /$SERVED_LEGAL_DIR/third-party-licenses.txt")
        elif ! unnamed="$(python3 -c '
import re, sys
label = set(sys.argv[1].split())
used = set()
for expression in re.findall(r"^==== \S+ \S+ \((.+)\) ====$", sys.stdin.read(), re.M):
    used.update(t for t in re.split(r"[\s()]+", expression) if t and t not in ("AND", "OR", "WITH"))
if not used:
    sys.exit("names no package")
print(" ".join(sorted(used - label)))
' "$licenses" <<< "$bundled")"; then
            legal+=("/$SERVED_LEGAL_DIR/third-party-licenses.txt names no package")
        elif [ -n "$unnamed" ]; then
            legal+=("its license label does not name $unnamed, which bundled packages use")
        fi
    fi
    docker rm "$container" >/dev/null
    count=0
    [ -n "$files" ] && count="$(printf '%s\n' "$files" | wc -l)"
    refused="$(printf '%s\n' "$files" | grep -E "$REFUSED" | grep -Ev "$ALLOWED_DOCS" || true)"
    problems=()
    [ -n "$refused" ] && problems+=("files not needed to run")
    [ -n "$scanner" ] && problems+=("the SBOM scanner is in the image")
    problems+=("${legal[@]}")
    read -r spdx subject version packages <<< "$sbom"
    case "$sbom" in
        absent) problems+=("no SBOM at /$SBOM_PATH") ;;
        unreadable) problems+=("the SBOM at /$SBOM_PATH is not a readable SPDX document") ;;
        *)
            [[ "$spdx" == SPDX-* ]] || problems+=("the SBOM states no SPDX version")
            [ "$subject" = "nodalarc-$component" ] || problems+=("the SBOM names $subject, expected nodalarc-$component")
            [ "$version" = "$labelled" ] || problems+=("the SBOM states version $version, the image label states $labelled")
            [ "$packages" -ge 1 ] || problems+=("the SBOM lists no package")
            ;;
    esac
    if [ "${#problems[@]}" -gt 0 ]; then
        failed=1
        echo "[contents] REFUSED $image: $count application files"
        printf '    %s\n' "${problems[@]}"
        printf '%s\n' "$refused" "$scanner" | grep . | sed 's/^/      /' || true
    else
        echo "[contents] ok      $image: $count application files; SBOM $spdx $subject $version, $packages packages; notices $notices"
    fi
done

if [ "$failed" -ne 0 ]; then
    echo "[contents] ERROR: an image failed the contents check" >&2
    exit 1
fi
