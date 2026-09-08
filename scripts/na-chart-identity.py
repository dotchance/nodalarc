#!/usr/bin/env python3
# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Identity of an assembled Helm chart, and whether a release runs it.

digest CHART_DIR
    Print the content digest of the chart: every file under templates/,
    files/ and crds/, plus values.yaml, by path and bytes. Chart.yaml is
    outside the digest; its version is build identity, not chart content.
    The assembler records this digest in Chart.yaml annotations, so the
    Helm release carries it in its chart metadata. Helm rewrites a stored
    chart's values under --reuse-values (defaults merged with the release
    configuration), so the stored values cannot be compared directly; the
    recorded digest can.

release-check CHART_DIR SECRETS_JSON NAMESPACE RELEASE
    SECRETS_JSON is the JSON list of the release's Helm secrets. The newest
    revision (by its numeric version label) must be deployed, and the digest
    its chart recorded must equal the assembled chart's. A match exits 0
    silently. A refusal prints the reason and exits 3.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import sys
from pathlib import Path

DIGEST_ANNOTATION = "nodalarc.io/chart-digest"
CONTENT_DIRS = ("templates", "files", "crds")
REFUSED = 3


def chart_content(chart_dir: Path) -> dict[str, bytes]:
    """The digest domain: every file under the content directories, plus values.yaml."""
    content: dict[str, bytes] = {}
    for sub in CONTENT_DIRS:
        for path in sorted((chart_dir / sub).rglob("*")):
            if path.is_file():
                content[path.relative_to(chart_dir).as_posix()] = path.read_bytes()
    values = chart_dir / "values.yaml"
    if not values.is_file():
        raise FileNotFoundError(f"{chart_dir} has no values.yaml")
    content["values.yaml"] = values.read_bytes()
    return content


def chart_digest(chart_dir: Path) -> str:
    digest = hashlib.sha256()
    for name, data in sorted(chart_content(chart_dir).items()):
        digest.update(f"{name}\0{len(data)}\0".encode())
        digest.update(data)
    return f"sha256:{digest.hexdigest()}"


class Refusal(Exception):
    pass


def _decode_release(payload: str) -> dict:
    return json.loads(gzip.decompress(base64.b64decode(base64.b64decode(payload))))


def release_check(chart_dir: Path, secrets_json: Path, namespace: str, release: str) -> None:
    items = json.loads(secrets_json.read_text()).get("items") or []
    if not items:
        raise Refusal(f"no Helm release secrets for release {release} in namespace {namespace}")
    revisions = []
    for item in items:
        metadata = item.get("metadata") or {}
        labels = metadata.get("labels") or {}
        try:
            version = int(labels["version"])
        except KeyError, TypeError, ValueError:
            raise Refusal(
                f"release secret {metadata.get('name')} carries no numeric version label"
            ) from None
        revisions.append((version, labels.get("status"), item))
    version, status, item = max(revisions, key=lambda revision: revision[0])
    if status != "deployed":
        raise Refusal(
            f"release {release} revision {version} is '{status}', not deployed; "
            "the running chart is not established"
        )
    payload = (item.get("data") or {}).get("release")
    if not payload:
        raise Refusal(f"release secret for revision {version} carries no release payload")
    chart = _decode_release(payload).get("chart") or {}
    recorded = ((chart.get("metadata") or {}).get("annotations") or {}).get(DIGEST_ANNOTATION)
    if not recorded:
        raise Refusal(
            f"release {release} revision {version} records no {DIGEST_ANNOTATION}; "
            "the identity of the chart it runs is not established"
        )
    local = chart_digest(chart_dir)
    if recorded == local:
        return
    # Templates, files and CRDs are stored as exact bytes and compare directly.
    # The digest domain is exactly those plus values.yaml, so when none of
    # them differs, values.yaml does.
    embedded = {
        entry["name"]: base64.b64decode(entry["data"])
        for entry in (chart.get("templates") or []) + (chart.get("files") or [])
    }
    content = chart_content(chart_dir)
    del content["values.yaml"]
    differing = sorted(
        name for name in set(content) | set(embedded) if content.get(name) != embedded.get(name)
    ) or ["values.yaml"]
    raise Refusal(
        f"release {release} revision {version} recorded chart {recorded}; "
        f"the assembled chart is {local}\n" + "\n".join(differing)
    )


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "digest":
        print(chart_digest(Path(argv[1])))
        return 0
    if len(argv) == 5 and argv[0] == "release-check":
        try:
            release_check(Path(argv[1]), Path(argv[2]), argv[3], argv[4])
        except Refusal as refusal:
            print(refusal)
            return REFUSED
        return 0
    print(
        "usage: na-chart-identity.py digest CHART_DIR\n"
        "       na-chart-identity.py release-check CHART_DIR SECRETS_JSON NAMESPACE RELEASE",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
