# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Every file a Dockerfile copies is among its component's declared inputs.

A release reuses a published image when the component's declared inputs are the
same as when that image was built. An input a Dockerfile copies and the list
leaves out would let a changed image ship under an unchanged version.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

# Component -> (Dockerfile, build context), as the Makefile builds each image.
IMAGES = {
    "ome": ("services/ome/Dockerfile", "."),
    "scheduler": ("services/scheduler/Dockerfile", "."),
    "node-agent": ("services/node_agent/Dockerfile", "."),
    "vs-api": ("services/vs_api/Dockerfile", "."),
    "operator": ("services/nodalarc_operator/Dockerfile", "."),
    "measurement": ("services/measurement/Dockerfile", "."),
    "probe": ("images/probe/Dockerfile", "."),
    "vf": ("frontend/Dockerfile", "frontend"),
    "base": ("images/base/Dockerfile", "images/base"),
    "frr": ("images/frr/Dockerfile", "images/frr"),
    "hdtn": ("images/hdtn/Dockerfile", "images/hdtn"),
    "ion": ("images/ion/Dockerfile", "images/ion"),
    "picoquic": ("images/picoquic/Dockerfile", "images/picoquic"),
    "ud3tn-tools": ("images/ud3tn-tools/Dockerfile", "images/ud3tn-tools"),
}
# Generated at build time from these declared inputs (Makefile _runtime-constraints).
GENERATED = {"build/runtime-constraints.txt": ("pyproject.toml", "uv.lock")}


def _inputs(name: str) -> list[str]:
    listed = subprocess.run(
        ["bash", str(ROOT / "scripts" / "na-images.sh"), "component-inputs", name],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert listed, name
    return listed


def _sources(dockerfile: Path) -> list[str]:
    sources = []
    for line in dockerfile.read_text().splitlines():
        words = line.split()
        if not words or words[0] not in ("COPY", "ADD"):
            continue
        arguments = [word for word in words[1:] if not word.startswith("--")]
        if any(word.startswith("--from=") for word in words[1:]):
            continue  # another stage or the named license context
        for source in arguments[:-1]:
            if re.match(r"^(https?://|\$\{)", source):
                continue  # a download pinned by checksum in the Dockerfile itself
            sources.append(source)
    return sources


def _covered(path: str, inputs: list[str]) -> bool:
    return any(path == item or path.startswith(item.rstrip("/") + "/") for item in inputs)


@pytest.mark.parametrize("name", sorted(IMAGES))
def test_every_copied_file_is_a_declared_input(name: str) -> None:
    dockerfile, context = IMAGES[name]
    inputs = _inputs(name)
    assert _covered(dockerfile, inputs), f"{name}: {dockerfile} is not an input"
    for source in _sources(ROOT / dockerfile):
        path = str(Path(context, source.rstrip("/"))).removeprefix("./")
        path = "." if path in ("", ".") else path
        if path == ".":
            path = context  # COPY . . copies the whole context
        required = GENERATED.get(path, (path,))
        for item in required:
            assert _covered(item, inputs), f"{name}: {dockerfile} copies {source}, not an input"


def test_every_input_exists() -> None:
    for name in IMAGES:
        for item in _inputs(name):
            assert (ROOT / item).exists(), f"{name}: declared input {item} does not exist"
