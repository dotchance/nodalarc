# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""A release chart's values come from the release record, and nothing is left unset."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "na-release-values.py"
SESSION = ["base", "frr", "hdtn", "ion", "picoquic", "ud3tn-tools"]
PLATFORM = ["ome", "scheduler", "node-agent", "vs-api", "operator", "vf"]
ZEROS = "0" * 64


def _record() -> dict:
    def image(name: str, digit: str) -> str:
        return f"ghcr.io/owner/nodalarc/{name}:0.8.1_abc@sha256:{digit * 64}"

    return {
        "release": "0.8.1",
        "commit": "abc",
        "session_images": SESSION,
        "components": {
            name: {"version": "0.8.1+abc", "image": image(name, "123456789abcdef"[index])}
            for index, name in enumerate(SESSION + PLATFORM)
        },
        "third_party": {
            "nats": {"image": f"ghcr.io/owner/nodalarc/nats:2.11-alpine@sha256:{'a' * 64}"},
            "nats-box": {"image": f"ghcr.io/owner/nodalarc/nats-box:0.19.3@sha256:{'b' * 64}"},
        },
    }


def _write(tmp_path: Path, source: Path) -> subprocess.CompletedProcess:
    record = tmp_path / "record.json"
    record.write_text(json.dumps(_record()))
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(source), str(record), str(tmp_path / "values.yaml")],
        capture_output=True,
        text=True,
        check=False,
    )


def test_every_release_value_comes_from_the_record(tmp_path: Path) -> None:
    result = _write(tmp_path, ROOT / "deploy" / "helm" / "values.yaml")
    assert result.returncode == 0, result.stderr
    values = yaml.safe_load((tmp_path / "values.yaml").read_text())
    record = _record()
    keys = {
        "base": "base",
        "ome": "ome",
        "vf": "vf",
        "scheduler": "scheduler",
        "node-agent": "nodeAgent",
        "vs-api": "vsApi",
        "operator": "operator",
    }
    for name, key in keys.items():
        assert values["images"][key] == record["components"][name]["image"]
    assert values["images"]["nats"] == record["third_party"]["nats"]["image"]
    assert values["images"]["natsBox"] == record["third_party"]["nats-box"]["image"]
    assert values["runtimeRelease"] == "0.8.1"
    assert values["buildTag"] == "0.8.1"
    assert json.loads(values["workloadImages"]) == {
        f"registry.example/nodalarc/{name}@sha256:{ZEROS}": record["components"][name]["image"]
        for name in SESSION
    }
    for image in values["images"].values():
        assert "@sha256:" in image, image


def test_a_source_missing_a_release_key_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "source.yaml"
    text = (ROOT / "deploy" / "helm" / "values.yaml").read_text()
    source.write_text(text.replace('workloadImages: ""', "workloadImages: {}"))
    result = _write(tmp_path, source)
    assert result.returncode != 0
    assert "workloadImages" in result.stderr


@pytest.mark.skipif(
    subprocess.run(["which", "helm"], capture_output=True).returncode != 0,
    reason="helm is not installed",
)
def test_the_release_chart_renders_every_image_pinned(tmp_path: Path) -> None:
    assert _write(tmp_path, ROOT / "deploy" / "helm" / "values.yaml").returncode == 0
    chart = tmp_path / "chart" / "nodalarc"
    subprocess.run(
        ["bash", str(ROOT / "scripts" / "na-render-helm-chart.sh"), "deploy/helm", str(chart)],
        cwd=ROOT,
        env={
            **__import__("os").environ,
            "PROJECT_VERSION": "0.8.1",
            "RELEASE_VALUES": str(tmp_path / "values.yaml"),
        },
        check=True,
        capture_output=True,
    )
    rendered = subprocess.run(
        ["helm", "template", "nodalarc", str(chart)], capture_output=True, text=True, check=True
    ).stdout
    images = [
        line.split("image:", 1)[1].strip().strip('"')
        for line in rendered.splitlines()
        if line.strip().startswith("image:")
    ]
    assert images
    for image in images:
        assert "@sha256:" in image and ZEROS not in image, image
