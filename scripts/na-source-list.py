# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Write a release's source list: where the source of every package in its images is.

Usage: na-source-list.py RELEASE RECORD_JSON OUTPUT_MARKDOWN

Every image in the release record is read from its registry. NodalArc's images
carry an SPDX report of every layer at /nodalarc/sbom.spdx.json; a mirrored
third-party image is scanned with the same pinned Syft image the build uses.
The list names, for each image, every Debian and Alpine package and every other
package whose recorded license names the GPL, LGPL or AGPL, with its exact
version and where its source is. It backs the written source offer in
THIRD_PARTY_NOTICES.md. Needs crane and docker; PLAIN_HTTP=1 reads a test
registry over plain HTTP.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

SBOM_PATH = "nodalarc/sbom.spdx.json"
SYFT_IMAGE = (
    "ghcr.io/anchore/syft:v1.52.0"
    "@sha256:500e2d872ac019436926e8322b4fc1f39441d94d21f6f4046c6ff29b30e8cb02"
)
COPYLEFT = ("GPL", "LGPL", "AGPL")


def fail(message: str) -> None:
    print(f"[source-list] ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def crane(*arguments: str) -> bytes:
    insecure = ["--insecure"] if os.environ.get("PLAIN_HTTP") == "1" else []
    result = subprocess.run(["crane", *arguments, *insecure], capture_output=True, check=False)
    if result.returncode != 0:
        fail(f"crane {' '.join(arguments)}: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def own_sbom(image: str) -> dict | None:
    """The report a NodalArc image carries, or None for an image without one."""
    with tarfile.open(fileobj=io.BytesIO(crane("export", image, "-")), mode="r|") as archive:
        for member in archive:
            if member.name.lstrip("./") == SBOM_PATH:
                extracted = archive.extractfile(member)
                return json.load(extracted) if extracted else None
    return None


def scanned_sbom(image: str) -> dict:
    with tempfile.TemporaryDirectory() as work:
        crane("pull", image, f"{work}/image.tar")
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-e",
                "SYFT_CHECK_FOR_APP_UPDATE=false",
                "-v",
                f"{work}:/work",
                SYFT_IMAGE,
                "scan",
                "docker-archive:/work/image.tar",
                "--scope",
                "all-layers",
                "-o",
                "spdx-json=/work/sbom.spdx.json",
                "--quiet",
            ],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            fail(f"syft could not scan {image}: {result.stderr.decode(errors='replace').strip()}")
        return json.loads(Path(work, "sbom.spdx.json").read_text())


def purl_of(package: dict) -> str | None:
    for reference in package.get("externalRefs", []):
        if reference.get("referenceType") == "purl":
            return reference["referenceLocator"]
    return None


def source_location(purl: str, name: str, version: str) -> str:
    parsed = urlparse(purl)
    kind = parsed.path.split("/", 1)[0]
    qualifiers = {key: values[0] for key, values in parse_qs(parsed.query).items()}
    if kind == "deb":
        source = unquote(qualifiers.get("upstream", name)).split("@", 1)[0]
        return f"https://snapshot.debian.org/package/{source}/{version}/"
    if kind == "apk":
        origin = unquote(qualifiers.get("upstream", name)).split("@", 1)[0]
        distro = qualifiers.get("distro", "")
        branch = distro.removeprefix("alpine-").rsplit(".", 1)[0]
        branch = f"{branch}-stable" if branch else "master"
        return f"https://gitlab.alpinelinux.org/alpine/aports/-/tree/{branch}?ref_type=heads ({origin})"
    if kind == "pypi":
        return f"https://pypi.org/project/{name}/{version}/#files"
    if kind == "npm":
        return f"https://www.npmjs.com/package/{name}/v/{version}"
    return purl


def license_of(package: dict) -> str:
    for key in ("licenseConcluded", "licenseDeclared"):
        value = package.get(key)
        if value and value != "NOASSERTION":
            return value
    return "not recorded"


def rows(sbom: dict) -> list[str]:
    listed = []
    for package in sbom.get("packages", []):
        purl = purl_of(package)
        if purl is None:
            continue
        kind = urlparse(purl).path.split("/", 1)[0]
        license_text = license_of(package)
        if kind not in ("deb", "apk") and not any(name in license_text for name in COPYLEFT):
            continue
        name, version = package["name"], package.get("versionInfo", "")
        location = source_location(purl, name, version)
        listed.append(f"| {name} | {version} | {license_text} | {location} |")
    return sorted(set(listed))


def main() -> None:
    if len(sys.argv) != 4:
        fail("usage: na-source-list.py RELEASE RECORD_JSON OUTPUT_MARKDOWN")
    release = sys.argv[1]
    record = json.loads(Path(sys.argv[2]).read_text())
    output = Path(sys.argv[3])
    images = [c["image"] for c in record["components"].values() if "image" in c]
    images += [c["image"] for c in record["third_party"].values()]

    lines = [
        f"# NodalArc {release}: source list",
        "",
        "Where the source of the packages in this release's images is. Every image",
        "carries the full report at /nodalarc/sbom.spdx.json, except the mirrored",
        "third-party images, which were scanned for this list. The written offer in",
        "THIRD_PARTY_NOTICES.md covers every package below.",
    ]
    for image in images:
        sbom = own_sbom(image) or scanned_sbom(image)
        lines += [
            "",
            f"## {image}",
            "",
            "| Package | Version | License | Source |",
            "| --- | --- | --- | --- |",
        ]
        lines += rows(sbom)
    output.write_text("\n".join(lines) + "\n")
    print(f"[source-list] {output}: {len(images)} image(s)")


if __name__ == "__main__":
    main()
