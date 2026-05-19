"""Project metadata consistency contracts."""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml
from nodalarc import project_info

ROOT = Path(__file__).resolve().parents[2]


def test_citation_does_not_pin_moving_repository_version() -> None:
    citation = yaml.safe_load((ROOT / "CITATION.cff").read_text(encoding="utf-8"))

    assert "version" not in citation
    assert "date-released" not in citation


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
    monkeypatch.setattr(project_info, "_installed_project_version", lambda: "0.1.0")

    assert project_info.project_version() == "0.4.2+3.gabc1234"


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
    assert result.stdout.strip() != "0.1.0"
