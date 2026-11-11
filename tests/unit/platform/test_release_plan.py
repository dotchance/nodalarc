# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""A release reuses a published image only when its version and its files are unchanged."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "na_release_plan", ROOT / "scripts" / "na-release-plan.py"
)
assert _spec is not None and _spec.loader is not None
plan = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = plan  # a dataclass resolves its module through sys.modules
_spec.loader.exec_module(plan)

PUBLISHED = {
    "version": "0.8.1+aaaa1111",
    "image": "ghcr.io/owner/nodalarc-frr:0.8.1_aaaa1111@sha256:" + "1" * 64,
}


def _digests(changed: bool):
    def digest(name: str, commit: str) -> str:
        return "new" if changed and commit == "HEAD" else "same"

    return digest


def test_an_unchanged_component_reuses_the_published_image() -> None:
    decision = plan.decide("frr", "0.8.1", PUBLISHED, _digests(False), "images/frr/VERSION")
    assert decision.action == "reuse"
    assert decision.version == PUBLISHED["version"]
    assert decision.image == PUBLISHED["image"]


def test_a_new_version_is_built() -> None:
    decision = plan.decide("frr", "0.8.2", PUBLISHED, _digests(True), "images/frr/VERSION")
    assert decision.action == "build"


def test_changed_files_under_an_unchanged_version_stop_the_release() -> None:
    decision = plan.decide("frr", "0.8.1", PUBLISHED, _digests(True), "images/frr/VERSION")
    assert decision.action == "stop"
    assert "images/frr/VERSION still says 0.8.1" in decision.reason


def test_a_component_no_release_published_is_built() -> None:
    assert (
        plan.decide("frr", "0.8.1", None, _digests(False), "images/frr/VERSION").action == "build"
    )


def test_the_published_commit_is_the_one_compared() -> None:
    seen: list[str] = []

    def digest(name: str, commit: str) -> str:
        seen.append(commit)
        return "same"

    plan.decide("frr", "0.8.1", PUBLISHED, digest, "images/frr/VERSION")
    assert sorted(seen) == ["HEAD", "aaaa1111"]
