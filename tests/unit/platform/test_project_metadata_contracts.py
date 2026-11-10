# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Project metadata consistency contracts."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
from pathlib import Path

import yaml
from nodalarc import project_info

ROOT = Path(__file__).resolve().parents[3]


def test_citation_does_not_pin_moving_repository_version() -> None:
    citation = yaml.safe_load((ROOT / "CITATION.cff").read_text(encoding="utf-8"))

    assert "version" not in citation
    assert "date-released" not in citation


def test_python_project_metadata_does_not_pin_release_version() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "project" not in pyproject
    assert "runtime" in pyproject["dependency-groups"]
    assert "dev" in pyproject["dependency-groups"]
    assert pyproject["tool"]["uv"]["default-groups"] == ["runtime", "dev"]
    assert not (ROOT / "lib/pyproject.toml").exists()


def test_uv_lock_does_not_pin_editable_project_version() -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))

    assert not any(
        package.get("name") == "nodal" and package.get("source", {}).get("editable") == "."
        for package in lock["package"]
    )


def test_frontend_package_metadata_does_not_pin_release_version() -> None:
    package = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))
    package_lock = json.loads((ROOT / "frontend/package-lock.json").read_text(encoding="utf-8"))

    assert "version" not in package
    assert "version" not in package_lock
    assert "version" not in package_lock["packages"][""]


def test_source_helm_chart_does_not_pin_release_version() -> None:
    chart_template = (ROOT / "deploy/helm/Chart.yaml.in").read_text(encoding="utf-8")

    assert not (ROOT / "deploy/helm/Chart.yaml").exists()
    assert "version: @PROJECT_VERSION@" in chart_template
    assert 'appVersion: "@PROJECT_VERSION@"' in chart_template


def test_project_info_accepts_exact_release_tags() -> None:
    assert project_info._version_from_git_describe("0.4.2") == "0.4.2"
    assert project_info._version_from_git_describe("nodalarc-0.4.2") == "0.4.2"


def test_project_info_accepts_release_marker_tags() -> None:
    assert project_info._version_from_git_describe("0.4.2-release") == "0.4.2"
    assert project_info._version_from_git_describe("nodalarc-0.4.2-release") == "0.4.2"


def test_project_info_marks_dirty_exact_tags_as_local_versions() -> None:
    assert project_info._version_from_git_describe("0.4.2-dirty") == "0.4.2+dirty"
    assert project_info._version_from_git_describe("nodalarc-0.4.2-dirty") == "0.4.2+dirty"
    assert project_info._version_from_git_describe("0.4.2-release-dirty") == "0.4.2+dirty"


def test_project_info_derives_post_tag_versions() -> None:
    assert project_info._version_from_git_describe("0.4.2-7-gabc1234") == "0.4.2+7.gabc1234"
    assert project_info._version_from_git_describe("0.4.2-release-7-gabc1234") == "0.4.2+7.gabc1234"
    assert (
        project_info._version_from_git_describe("nodalarc-0.4.2-7-gabc1234-dirty")
        == "0.4.2+7.gabc1234.dirty"
    )


def test_project_info_uses_explicit_runtime_version(monkeypatch) -> None:
    monkeypatch.setenv("NODALARC_VERSION", "0.4.2")

    assert project_info.project_version() == "0.4.2"


def test_project_version_script_uses_explicit_runtime_version() -> None:
    env = os.environ.copy()
    env["NODALARC_VERSION"] = "0.4.2"
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/na-project-version.sh")],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.stdout.strip() == "0.4.2"


def test_project_version_script_matches_python_version_contract(monkeypatch) -> None:
    monkeypatch.delenv("NODALARC_VERSION", raising=False)
    env = os.environ.copy()
    env.pop("NODALARC_VERSION", None)

    result = subprocess.run(
        ["bash", str(ROOT / "scripts/na-project-version.sh")],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )

    version = result.stdout.strip()
    assert version == project_info.project_version()
    assert re.fullmatch(r"[0-9]+(?:[.][0-9A-Za-z]+)*(?:[+][0-9A-Za-z.]+)?", version)


def test_helm_chart_renderer_injects_project_version(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PROJECT_VERSION"] = "0.4.2"
    output_chart = tmp_path / "chart"

    result = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts/na-render-helm-chart.sh"),
            "deploy/helm",
            str(output_chart),
        ],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )

    chart = yaml.safe_load((output_chart / "Chart.yaml").read_text(encoding="utf-8"))
    assert result.stdout.strip() == str(output_chart)
    assert chart["version"] == "0.4.2"
    assert chart["appVersion"] == "0.4.2"


_SOURCE_PATTERNS = (
    "*.py",
    "*.ts",
    "*.tsx",
    "*.js",
    "*.mjs",
    "*.sh",
    "*.css",
    "*.j2",
    "*.proto",
    # Build, deployment and workflow sources.
    "Makefile",
    "*Dockerfile",
    "deploy/helm/*.yaml",
    "deploy/helm/*.in",
    "configs/platform.yaml",
    "config.mk.example",
    "pyproject.toml",
    ".pre-commit-config.yaml",
    ".github/*.yml",
    "frontend/nginx.conf",
    "frontend/index.html",
)
_SPDX_LINE = re.compile(r"SPDX-License-Identifier: (AGPL-3\.0-only(?: AND [A-Za-z0-9.-]+)*)\b")
# AGPL-3.0 Section 7: a file under additional terms says where they are.
_NOTICE_POINTER = "Additional terms under AGPL-3.0 Section 7 are in the NOTICE file."


def _tracked_files(*patterns: str) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--", *patterns],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.split()


def test_every_tracked_source_file_names_the_project_license() -> None:
    # The license of a file is read from its own header (REUSE practice), so a
    # file without one is unlicensed in the eyes of every tool that scans it.
    missing = []
    for name in _tracked_files(*_SOURCE_PATTERNS):
        head = "\n".join((ROOT / name).read_text(encoding="utf-8").splitlines()[:6])
        if (
            project_info.PROJECT_COPYRIGHT not in head
            or not _SPDX_LINE.search(head)
            or _NOTICE_POINTER not in head
        ):
            missing.append(name)
    assert missing == []


def test_every_catalog_file_carries_the_second_license() -> None:
    # NOTICE licenses catalog/ under AGPL-3.0-only OR CC0-1.0; the header of
    # each file carries that choice wherever the file is copied to.
    expected = (
        f"# {project_info.PROJECT_COPYRIGHT}\n"
        "# SPDX-License-Identifier: AGPL-3.0-only OR CC0-1.0\n"
        f"# {_NOTICE_POINTER}\n"
    )
    files = _tracked_files("catalog/*.yaml")
    assert files
    assert [
        n for n in files if not (ROOT / n).read_text(encoding="utf-8").startswith(expected)
    ] == []


def test_every_license_statement_names_the_same_license() -> None:
    license_id = project_info.PROJECT_LICENSE
    citation = yaml.safe_load((ROOT / "CITATION.cff").read_text(encoding="utf-8"))
    package = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))
    package_lock = json.loads((ROOT / "frontend/package-lock.json").read_text(encoding="utf-8"))
    # An image that delivers an upstream program, or a page bundling npm
    # packages, also names those licenses in its label; the label of every
    # other image names NodalArc's alone.
    upstream_program_images = {
        "frontend/Dockerfile",
        "images/frr/Dockerfile",
        "images/hdtn/Dockerfile",
        "images/ion/Dockerfile",
        "images/picoquic/Dockerfile",
        "images/ud3tn-tools/Dockerfile",
    }
    labels = {
        name: re.findall(
            r'org\.opencontainers\.image\.licenses="([^"]+)"',
            (ROOT / name).read_text(encoding="utf-8"),
        )
        for name in _tracked_files("*Dockerfile")
        if name not in upstream_program_images
    }

    assert license_id == "AGPL-3.0-only"
    assert citation["license"] == license_id
    assert package["license"] == license_id
    assert package_lock["packages"][""]["license"] == license_id
    assert labels and all(found == [license_id] for found in labels.values()), labels
    assert (
        (ROOT / "LICENSE")
        .read_text(encoding="utf-8")
        .lstrip()
        .startswith(
            "GNU AFFERO GENERAL PUBLIC LICENSE\n                       Version 3, 19 November 2007"
        )
    )
    assert "GNU Affero General Public License, version 3 only" in (ROOT / "NOTICE").read_text(
        encoding="utf-8"
    )
