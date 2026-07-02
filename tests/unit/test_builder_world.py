# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Builder resolved-world tests — the read-only world behind the session builder.

``build_builder_world`` must run the same resolve → OME-inputs →
``build_session_ephemeris`` chain the OME main loop runs at session start.
These tests pin node-set parity with the resolver, ephemeris variant
correctness per node kind, epoch derivation from the session time block, and
typed rejection of bad or non-session references.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from nodalarc.catalog_paths import CatalogPathError
from nodalarc.models.builder_world import BuilderWorld
from nodalarc.models.events import EphemerisNodeFixed, EphemerisNodeKeplerian
from nodalarc.resolve_session import resolve_session
from ome.builder_world import build_builder_world
from vs_api.main import app

_WALKER_REF = "nodalarc:sessions/earth-leo-walker.yaml"
_WALKER_PATH = Path("catalog/nodalarc/sessions/earth-leo-walker.yaml")

client = TestClient(app)


@pytest.fixture(scope="module")
def walker_world() -> BuilderWorld:
    return build_builder_world(_WALKER_REF)


def test_returns_builder_world(walker_world):
    assert isinstance(walker_world, BuilderWorld)
    assert walker_world.session.name


def test_node_set_matches_resolver(walker_world):
    raw = yaml.safe_load(_WALKER_PATH.read_text(encoding="utf-8"))
    resolved = resolve_session(raw)
    resolved_ids = {node.node_id for node in resolved.nodes}
    assert {node.node_id for node in walker_world.nodes} == resolved_ids
    # The ephemeris is OME's physics-participant view, a subset of the world:
    # every satellite propagates, but only link-rule-selected ground nodes
    # appear. The world node list is the node universe.
    assert set(walker_world.ephemeris.nodes) <= resolved_ids
    satellite_ids = {n.node_id for n in walker_world.nodes if n.kind == "satellite"}
    assert satellite_ids <= set(walker_world.ephemeris.nodes)


def test_kind_matches_ephemeris_variant(walker_world):
    satellites = 0
    grounds = 0
    for node in walker_world.nodes:
        entry = walker_world.ephemeris.nodes.get(node.node_id)
        if node.kind == "satellite":
            satellites += 1
            assert node.surface_position is None
            assert isinstance(entry, EphemerisNodeKeplerian)
            assert entry.segment_id == node.segment_id
        elif node.kind == "ground_station":
            grounds += 1
            assert node.surface_position is not None
            if entry is not None:
                assert isinstance(entry, EphemerisNodeFixed)
                assert entry.segment_id == node.segment_id
                assert entry.lat_deg == node.surface_position.lat_deg
                assert entry.lon_deg == node.surface_position.lon_deg
    assert satellites > 0
    assert grounds > 0


def test_nodes_carry_hardware_and_network_facts(walker_world):
    """Inspector facts ride the resolved models verbatim: every node has its
    terminal inventory; ground nodes carry interfaces and (for the walker's
    gateways) originated prefixes."""
    for node in walker_world.nodes:
        assert node.terminal_inventory, f"{node.node_id} has no terminal inventory"
        for block in node.terminal_inventory:
            assert block.owner_node_id == node.node_id
        if node.kind == "ground_station":
            assert node.interfaces is not None
            assert node.interfaces.lo0.ipv4 or node.interfaces.lo0.ipv6


def test_link_rules_project_resolved_endpoint_membership(walker_world):
    """Rules ride the wire as flat display facts; endpoint node ids are the
    resolver's runtime ids. When a rule declares explicit pairs, the pair ids
    must be resolvable against the world (pins the G1-class identity question
    empirically: whatever id space explicit pairs use, the builder must be
    able to join them to nodes)."""
    assert walker_world.link_rules, "walker session has link rules"
    world_ids = {node.node_id for node in walker_world.nodes}
    for rule in walker_world.link_rules:
        assert rule.topology_mode
        for endpoint in rule.endpoints:
            assert endpoint.node_ids
            assert set(endpoint.node_ids) <= world_ids
        if rule.topology_mode == "explicit_pairs":
            for a, b in rule.explicit_pairs:
                assert a in world_ids, f"explicit pair id {a!r} is not a runtime node id"
                assert b in world_ids, f"explicit pair id {b!r} is not a runtime node id"


def test_ground_node_without_space_links_stays_in_world(walker_world):
    """Denver gw2 is a MEO gateway no LEO-session link rule selects: OME's
    ephemeris omits it, but it is a resolved node and must stay in the world
    with its surface position. This exact node vanished when the world was
    first derived from the ephemeris alone."""
    by_id = {node.node_id: node for node in walker_world.nodes}
    gw2 = by_id["earth-us-co-denver-gw2"]
    assert gw2.kind == "ground_station"
    assert gw2.surface_position is not None
    assert "earth-us-co-denver-gw2" not in walker_world.ephemeris.nodes


def test_epoch_derived_from_session_time(walker_world):
    raw = yaml.safe_load(_WALKER_PATH.read_text(encoding="utf-8"))
    start = datetime.fromisoformat(raw["time"]["start_time"].replace("Z", "+00:00"))
    assert start.tzinfo is not None
    assert walker_world.epoch_unix == start.timestamp()
    assert walker_world.ephemeris.epoch_unix == walker_world.epoch_unix
    assert walker_world.ephemeris.epoch_id == 0


def test_body_frames_carry_physical_facts(walker_world):
    assert "earth" in walker_world.ephemeris.body_frames
    earth = walker_world.ephemeris.body_frames["earth"]
    assert earth.equatorial_radius_km > 6000
    assert earth.gravitational_parameter_km3_s2 > 0


def test_traversal_reference_rejected():
    with pytest.raises(CatalogPathError):
        build_builder_world("nodalarc:../secrets.yaml")


def test_non_nodalarc_root_rejected():
    with pytest.raises(CatalogPathError):
        build_builder_world("user:sessions/earth-leo-walker.yaml")


def test_non_session_document_rejected():
    with pytest.raises(ValueError):
        build_builder_world("nodalarc:orbits/earth/leo/earth-leo-starlink.yaml")


def test_endpoint_requires_exactly_one_input_form():
    for body in ({}, {"source": _WALKER_REF, "session": "x"}):
        response = client.post("/api/v1/builder/resolve-world", json=body)
        assert response.status_code == 400
        assert response.json()["error"] == "provide exactly one of source or session"


class _FakeSessionManager:
    def _validated_session_path(self, session_path: str) -> Path | None:
        if session_path == "catalog/nodalarc/sessions/earth-leo-walker.yaml":
            return _WALKER_PATH
        return None


def test_endpoint_resolves_scanned_session_key(monkeypatch):
    import vs_api.main as main

    monkeypatch.setattr(main, "_session_manager", _FakeSessionManager())
    response = client.post(
        "/api/v1/builder/resolve-world",
        json={"session": "catalog/nodalarc/sessions/earth-leo-walker.yaml"},
    )
    assert response.status_code == 200
    assert response.json()["session"]["name"]


def test_endpoint_rejects_unknown_session_key(monkeypatch):
    import vs_api.main as main

    monkeypatch.setattr(main, "_session_manager", _FakeSessionManager())
    response = client.post(
        "/api/v1/builder/resolve-world",
        json={"session": "not/in/the/scan.yaml"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "Unknown session file"


def test_endpoint_session_key_requires_session_manager(monkeypatch):
    import vs_api.main as main

    monkeypatch.setattr(main, "_session_manager", None)
    response = client.post(
        "/api/v1/builder/resolve-world",
        json={"session": "catalog/nodalarc/sessions/earth-leo-walker.yaml"},
    )
    assert response.status_code == 503


def test_endpoint_rejects_bad_reference():
    response = client.post(
        "/api/v1/builder/resolve-world",
        json={"source": "nodalarc:../secrets.yaml"},
    )
    assert response.status_code == 400


def test_endpoint_returns_world():
    response = client.post(
        "/api/v1/builder/resolve-world",
        json={"source": _WALKER_REF},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["session"]["name"]
    assert payload["ephemeris"]["epoch_id"] == 0
    assert len(payload["nodes"]) >= len(payload["ephemeris"]["nodes"])
