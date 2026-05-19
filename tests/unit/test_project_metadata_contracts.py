"""Project metadata consistency contracts."""

from __future__ import annotations

import json
import os
import subprocess
import tomllib
from pathlib import Path

import yaml
from nodalarc import project_info

ROOT = Path(__file__).resolve().parents[2]


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


def test_project_info_marks_dirty_exact_tags_as_local_versions() -> None:
    assert project_info._version_from_git_describe("0.4.2-dirty") == "0.4.2+dirty"
    assert project_info._version_from_git_describe("nodalarc-0.4.2-dirty") == "0.4.2+dirty"


def test_project_info_derives_post_tag_versions() -> None:
    assert project_info._version_from_git_describe("0.4.2-7-gabc1234") == "0.4.2+7.gabc1234"
    assert (
        project_info._version_from_git_describe("nodalarc-0.4.2-7-gabc1234-dirty")
        == "0.4.2+7.gabc1234.dirty"
    )


def test_project_info_uses_explicit_runtime_version(monkeypatch) -> None:
    monkeypatch.setenv("NODALARC_VERSION", "0.4.2")

    assert project_info.project_version() == "0.4.2"


def test_project_info_uses_git_describe_before_installed_metadata(monkeypatch) -> None:
    class Result:
        stdout = "0.4.2-3-gabc1234\n"

    def fake_run(*args: object, **kwargs: object) -> Result:
        return Result()

    monkeypatch.delenv("NODALARC_VERSION", raising=False)
    monkeypatch.setattr(project_info.subprocess, "run", fake_run)
    monkeypatch.setattr(project_info, "_installed_project_version", lambda: "installed-metadata")

    assert project_info.project_version() == "0.4.2+3.gabc1234"


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


def test_project_version_script_uses_tagged_git_version() -> None:
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/na-project-version.sh")],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.stdout.strip()


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
