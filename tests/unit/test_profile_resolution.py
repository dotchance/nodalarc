# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Effective workload-profile resolution: three levels, provenance, refusal."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from nodalarc.resolve_session import CatalogRoots, SessionResolutionError, resolve_session

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_ROOT = ROOT / "catalog" / "nodalarc"
SIMPLE_SESSION = SHIPPED_ROOT / "sessions" / "earth-leo-simple.yaml"
QUIC_SESSION = SHIPPED_ROOT / "sessions" / "earth-luna-quic.yaml"
BASE_SITE = SHIPPED_ROOT / "sites" / "earth" / "de" / "earth-de-berlin.yaml"
BASE_NODE = SHIPPED_ROOT / "nodes" / "ground" / "starlink-gateway.yaml"

FRR_PROFILE = "nodalarc:profiles/frr-router.yaml"
USER_PROFILE = "user:profiles/override-profile.yaml"


def _write_yaml(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def _user_profile_document(profile_id: str) -> dict[str, Any]:
    return {
        "profile": {
            "id": profile_id,
            "registry": "registry.example",
            "image": f"nodalarc/base@sha256:{'0' * 64}",
            "command": ["/bin/bash", "-c", "sleep infinity"],
            "resources": {
                "requests": {"cpu_m": 10, "memory_mi": 16},
                "limits": {"cpu_m": 100, "memory_mi": 64},
            },
        }
    }


def _session_with_user_ground(
    tmp_path: Path,
    *,
    site_mutation=None,
    node_mutation=None,
    segment_mutation=None,
) -> tuple[dict[str, Any], CatalogRoots]:
    user_root = tmp_path / "user"
    _write_yaml(user_root / "profiles" / "override-profile.yaml", _user_profile_document("override-profile"))

    node_document = yaml.safe_load(BASE_NODE.read_text(encoding="utf-8"))
    node_document["node"]["id"] = "profile-test-node"
    if node_mutation is not None:
        node_mutation(node_document["node"])
    _write_yaml(user_root / "nodes" / "profile-test-node.yaml", node_document)

    site_document = yaml.safe_load(BASE_SITE.read_text(encoding="utf-8"))
    site_document["site"]["id"] = "profile-test-site"
    for site_node in site_document["site"]["nodes"]:
        site_node["node"] = "user:nodes/profile-test-node.yaml"
    if site_mutation is not None:
        site_mutation(site_document["site"])
    _write_yaml(user_root / "sites" / "profile-test-site.yaml", site_document)
    _write_yaml(
        user_root / "site-sets" / "profile-test-sites.yaml",
        {"site_set": {"id": "profile-test-sites", "sites": ["user:sites/profile-test-site.yaml"]}},
    )

    session = yaml.safe_load(SIMPLE_SESSION.read_text(encoding="utf-8"))
    ground_segment = next(segment for segment in session["segments"] if "placement" in segment)
    ground_segment["placement"]["from_site_set"] = "user:site-sets/profile-test-sites.yaml"
    if segment_mutation is not None:
        segment_mutation(session, ground_segment)
    roots = CatalogRoots.from_catalog_root(SHIPPED_ROOT, user_root=user_root)
    return session, roots


def test_every_shipped_simple_session_node_inherits_its_definition_default() -> None:
    resolution = resolve_session(yaml.safe_load(SIMPLE_SESSION.read_text(encoding="utf-8")))

    assert resolution.nodes
    for node in resolution.nodes:
        assert node.profile == FRR_PROFILE
        assert node.profile_level == "node_definition"


def test_segment_profile_overrides_the_node_definition(tmp_path: Path) -> None:
    def override_segment(session, ground_segment):
        ground_segment["profile"] = USER_PROFILE

    session, roots = _session_with_user_ground(tmp_path, segment_mutation=override_segment)
    resolution = resolve_session(session, catalog_roots=roots)

    ground = [node for node in resolution.nodes if node.kind == "ground_station"]
    assert ground
    for node in ground:
        assert node.profile == USER_PROFILE
        assert node.profile_level == "segment"
    satellites = [node for node in resolution.nodes if node.kind == "satellite"]
    assert all(node.profile_level == "node_definition" for node in satellites)


def test_placed_node_profile_overrides_segment_and_definition(tmp_path: Path) -> None:
    def override_segment(session, ground_segment):
        ground_segment["profile"] = FRR_PROFILE

    def override_site(site):
        site["nodes"][0]["profile"] = USER_PROFILE

    session, roots = _session_with_user_ground(
        tmp_path, segment_mutation=override_segment, site_mutation=override_site
    )
    resolution = resolve_session(session, catalog_roots=roots)

    overridden = [node for node in resolution.nodes if node.profile == USER_PROFILE]
    assert len(overridden) == 1
    assert overridden[0].profile_level == "node"


def test_missing_profile_at_every_level_is_refused(tmp_path: Path) -> None:
    def strip_default(node):
        node.pop("profile", None)

    session, roots = _session_with_user_ground(tmp_path, node_mutation=strip_default)

    with pytest.raises(SessionResolutionError, match="no workload profile at any level"):
        resolve_session(session, catalog_roots=roots)


def test_conflicting_shared_site_segment_profiles_are_refused(tmp_path: Path) -> None:
    def add_conflicting_segment(session, ground_segment):
        ground_segment["profile"] = USER_PROFILE
        duplicate = dict(ground_segment)
        duplicate["id"] = "ground-two"
        duplicate["profile"] = FRR_PROFILE
        session["segments"].append(duplicate)

    session, roots = _session_with_user_ground(
        tmp_path, segment_mutation=add_conflicting_segment
    )

    with pytest.raises(SessionResolutionError, match="conflicting\\s+profile statements"):
        resolve_session(session, catalog_roots=roots)


def test_profile_reference_must_load_a_profile_document(tmp_path: Path) -> None:
    # A dangling profile reference fails the same way every dangling catalog
    # reference fails today: loudly, from the reference resolver. The
    # deployment loader rejects it with its own typed message before
    # resolution in the real path.
    def dangling(site):
        site["nodes"][0]["profile"] = "user:profiles/absent.yaml"

    session, roots = _session_with_user_ground(tmp_path, site_mutation=dangling)

    with pytest.raises(FileNotFoundError):
        resolve_session(session, catalog_roots=roots)

    def wrong_family(site):
        site["nodes"][0]["profile"] = "user:profiles/wrong-family.yaml"

    session, roots = _session_with_user_ground(tmp_path / "wrong", site_mutation=wrong_family)
    _write_yaml(
        (tmp_path / "wrong" / "user") / "profiles" / "wrong-family.yaml",
        {"node": {"id": "wrong-family", "forwarding": "host", "ethernet": [], "terminals": [], "payloads": []}},
    )

    with pytest.raises(SessionResolutionError):
        resolve_session(session, catalog_roots=roots)


def test_shipped_quic_session_records_node_level_endpoint_profiles() -> None:
    resolution = resolve_session(yaml.safe_load(QUIC_SESSION.read_text(encoding="utf-8")))

    by_profile: dict[str, list[str]] = {}
    for node in resolution.nodes:
        by_profile.setdefault(node.profile, []).append(node.node_id)

    assert any("picoquic-client" in profile for profile in by_profile)
    assert any("picoquic-server" in profile for profile in by_profile)
    endpoint_nodes = [
        node
        for node in resolution.nodes
        if "picoquic" in node.profile
    ]
    assert endpoint_nodes
    assert all(node.profile_level == "node" for node in endpoint_nodes)
    routers = [node for node in resolution.nodes if node.profile == FRR_PROFILE]
    assert routers
    assert all(node.profile_level == "node_definition" for node in routers)
