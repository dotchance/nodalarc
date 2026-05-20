# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Project attribution and build metadata shared by API and tooling surfaces."""

from __future__ import annotations

import os
import re
import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

PROJECT_NAME = "NodalArc"
PROJECT_PACKAGE_NAMES = ("nodal", "nodalarc")
PROJECT_AUTHOR = ".chance (dotchance)"
PROJECT_COPYRIGHT = "Copyright 2024-2026 .chance (dotchance)"
PROJECT_SOURCE_URL = "https://github.com/dotchance/nodalarc"
PROJECT_URL = "https://nodal.asmolab.net"
PROJECT_LICENSE = "Apache-2.0"
PROJECT_NOTICE = "See NOTICE and THIRD_PARTY_NOTICES.md."
UNKNOWN_VERSION = "0.0.0+unknown"
UNKNOWN_BUILD_VALUE = "unknown"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _installed_project_version() -> str | None:
    for package_name in PROJECT_PACKAGE_NAMES:
        try:
            return version(package_name)
        except PackageNotFoundError:
            continue
    return None


def _version_from_git_describe(described: str) -> str | None:
    value = described.strip()
    if not value:
        return None

    value = value.removeprefix("nodalarc-")
    dirty_suffix = ""
    if value.endswith("-dirty"):
        dirty_suffix = ".dirty"
        value = value.removesuffix("-dirty")

    def normalize_release_marker(tag_base: str) -> str:
        return tag_base.removesuffix("-release")

    value = normalize_release_marker(value)

    if re.fullmatch(r"[0-9]+(?:[.][0-9A-Za-z]+)*", value):
        return f"{value}+dirty" if dirty_suffix else value

    if match := re.fullmatch(r"(.+)-([0-9]+)-g([0-9a-f]+)", value):
        base, commits, revision = match.groups()
        base = normalize_release_marker(base)
        return f"{base}+{commits}.g{revision}{dirty_suffix}"

    if re.fullmatch(r"[0-9a-f]{7,40}", value):
        return f"0.0.0+g{value}{dirty_suffix}"

    return None


def _git_described_project_version() -> str | None:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(_repo_root()),
                "describe",
                "--tags",
                "--match",
                "nodalarc-[0-9]*",
                "--match",
                "[0-9]*",
                "--dirty",
                "--always",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except FileNotFoundError, subprocess.SubprocessError:
        return None

    return _version_from_git_describe(result.stdout)


def project_version() -> str:
    return (
        os.environ.get("NODALARC_VERSION")
        or _git_described_project_version()
        or _installed_project_version()
        or UNKNOWN_VERSION
    )


def _git_revision() -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(_repo_root()), "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except FileNotFoundError, subprocess.SubprocessError:
        return None

    revision = result.stdout.strip()
    return revision or None


def project_revision() -> str:
    return (
        os.environ.get("NODALARC_BUILD_REVISION")
        or os.environ.get("VCS_REF")
        or _git_revision()
        or UNKNOWN_BUILD_VALUE
    )


def project_build_date() -> str:
    return (
        os.environ.get("NODALARC_BUILD_DATE") or os.environ.get("BUILD_DATE") or UNKNOWN_BUILD_VALUE
    )


def project_attribution() -> dict[str, str]:
    return {
        "name": PROJECT_NAME,
        "version": project_version(),
        "revision": project_revision(),
        "build_date": project_build_date(),
        "author": PROJECT_AUTHOR,
        "copyright": PROJECT_COPYRIGHT,
        "source": PROJECT_SOURCE_URL,
        "url": PROJECT_URL,
        "license": PROJECT_LICENSE,
        "notice": PROJECT_NOTICE,
    }
