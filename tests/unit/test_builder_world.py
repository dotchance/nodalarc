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
        assert response.json()["error"] == "provide exactly one of source, session, or document"


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
    payload = response.json()
    assert payload["world"]["session"]["name"]
    # The resolve-check ships the session as a parsed mapping too — the
    # builder imports it to edit an existing (e.g. the running) session,
    # and it must be the file's content verbatim.
    assert payload["document"] == yaml.safe_load(_WALKER_PATH.read_text(encoding="utf-8"))


def test_resolve_check_ships_the_document_verbatim():
    raw = yaml.safe_load(_WALKER_PATH.read_text(encoding="utf-8"))
    response = client.post("/api/v1/builder/resolve-world", json={"document": raw})
    assert response.status_code == 200
    assert response.json()["document"] == raw


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


def test_endpoint_resolves_inline_document():
    raw = yaml.safe_load(_WALKER_PATH.read_text(encoding="utf-8"))
    response = client.post("/api/v1/builder/resolve-world", json={"document": raw})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["world"]["nodes"]) == 181
    # The canonical YAML round-trips to the same document (one serializer).
    assert yaml.safe_load(payload["document_yaml"]) == raw


def test_endpoint_document_errors_return_resolver_message():
    response = client.post(
        "/api/v1/builder/resolve-world",
        json={"document": {"session": {"name": "x"}, "segments": []}},
    )
    assert response.status_code == 422
    # The resolver's own message comes back verbatim - it is the user's
    # validation surface, not a hidden internal detail.
    assert response.json()["error"]


def test_catalog_browse_lists_validated_primitives():
    response = client.get("/api/v1/builder/catalog", params={"family": "nodes"})
    assert response.status_code == 200
    entries = response.json()
    assert entries
    starlink = next(e for e in entries if e["id"] == "starlink-v2-mesh")
    assert starlink["ref"] == "nodalarc:nodes/space/starlink-v2-mesh.yaml"
    assert starlink["error"] is None
    assert all(e["family"] == "nodes" for e in entries)


def test_catalog_browse_rejects_unknown_family():
    response = client.get("/api/v1/builder/catalog", params={"family": "../secrets"})
    assert response.status_code == 400


def test_catalog_object_read_round_trips_grammar_document():
    response = client.get(
        "/api/v1/builder/catalog/object",
        params={"ref": "nodalarc:orbits/earth/leo/earth-leo-starlink.yaml"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["family_wrapper"] == "orbit"
    assert "orbit" in payload["document"]
    assert payload["document"]["orbit"]["id"]


def test_endpoint_returns_world():
    response = client.post(
        "/api/v1/builder/resolve-world",
        json={"source": _WALKER_REF},
    )
    assert response.status_code == 200
    payload = response.json()["world"]
    assert payload["session"]["name"]
    assert payload["ephemeris"]["epoch_id"] == 0
    assert len(payload["nodes"]) >= len(payload["ephemeris"]["nodes"])
    # The world speaks the user's names: every segment carries the authored
    # display name (source, segment, or site-set), never just a runtime id.
    names = {seg["segment_id"]: seg["display_name"] for seg in payload["segments"]}
    assert set(names) == {node["segment_id"] for node in payload["nodes"]}
    assert names["leo"] == "Earth LEO Walker-delta 176-satellite shell"


def test_segment_display_names_reads_every_grammar_home():
    """The helper must read names everywhere the resolver itself accepts a
    source: segment-level, wrapped inline, ref, and the bare inline site-set
    body that _load_expected allows (found by review — _load_ref_or_object
    alone silently dropped it)."""
    from nodalarc.resolve_session import default_catalog_roots, segment_display_names

    roots = default_catalog_roots()
    raw = {
        "segments": [
            {"id": "own", "display_name": "Named On Segment", "source": {"x": {}}},
            {
                "id": "wrapped",
                "source": "nodalarc:constellations/earth/leo/earth-leo-walker-delta-176.yaml",
            },
            {
                "id": "bare-ground",
                "placement": {
                    "from_site_set": {
                        "id": "east-sites",
                        "display_name": "Eastern ground sites",
                        "sites": [
                            {
                                "id": "s1",
                                "display_name": "Site 1",
                                "location": {"lat_deg": 1.0, "lon_deg": 2.0, "alt_m": 0.0},
                                "node": "nodalarc:nodes/ground/earth-leo-gateway.yaml",
                            }
                        ],
                    }
                },
            },
        ]
    }
    names = segment_display_names(raw, roots=roots)
    assert names["own"] == "Named On Segment"
    assert names["wrapped"] == "Earth LEO Walker-delta 176-satellite shell"
    assert names["bare-ground"] == "Eastern ground sites"


def test_world_ships_allocator_capacity_facts():
    """Capacity truth is computed once, by the allocator, and shipped: the
    world carries per-rule allocated pairs and per-node matching/free
    interface counts derived from the same eligibility body allocation uses.
    Displays report these; no client re-derivation."""
    raw = yaml.safe_load(_WALKER_PATH.read_text(encoding="utf-8"))
    response = client.post("/api/v1/builder/resolve-world", json={"document": raw})
    assert response.status_code == 200
    world = response.json()["world"]
    allocations = {a["rule_id"]: a for a in world["allocations"]}
    assert allocations, "world carries no allocation facts"
    candidates = world["link_candidates"]
    for rule_id, alloc in allocations.items():
        assert alloc["allocated_pairs"] == sum(1 for c in candidates if c["rule_id"] == rule_id)
        for row in alloc["per_node"]:
            assert 0 <= row["free"] <= row["matching"]
    # The walker's fixed ISL mesh consumes every isl interface: 176 sats,
    # 4 isl mounts each, 352 pairs — so free must be exactly 0 with 4
    # matching on every member. A facts implementation that ignores the
    # allocator's used-set (free == matching) fails here.
    isl = allocations["leo_isl"]
    assert isl["allocated_pairs"] == 352
    assert isl["per_node"], "isl allocation carries no per-node facts"
    for row in isl["per_node"]:
        assert (row["matching"], row["free"]) == (4, 0)
    # Access consumes nothing at resolve time: free mirrors matching.
    for row in allocations["leo_access"]["per_node"]:
        assert row["free"] == row["matching"]


def test_interface_wall_ships_draft_addressable_subject():
    """A refusal that names an object lands on that object: the allocator
    wall carries the failing rule id and the node's segment id — both
    draft-addressable — plus the runtime node id as display detail."""
    raw = yaml.safe_load(_WALKER_PATH.read_text(encoding="utf-8"))
    # Oversubscribe: pile explicit fixed pairs onto one node until it runs
    # out of matching interfaces.
    fixed = next(
        rule
        for rule in raw["link_rules"]
        if rule.get("topology", {}).get("mode") == "explicit_pairs"
    )
    fixed["topology"]["pairs"] = [
        {"a": "sat-p00s00", "b": f"sat-p00s{slot:02d}"} for slot in range(1, 11)
    ]
    response = client.post("/api/v1/builder/resolve-world", json={"document": raw})
    assert response.status_code == 422
    body = response.json()
    assert "needs another fixed interface" in body["error"]
    assert body["subject"]["kind"] == "link_rule"
    assert body["subject"]["id"] == fixed["id"]
    assert body["segment_id"]
    assert body["node_id"]


def test_unsupported_feature_wall_ships_typed_features():
    """UnsupportedFeatureError is typed at the raise site; the envelope must
    not flatten it to prose. A bgp routing domain is grammar-valid and
    deterministically runtime-gated, so the typed gate — not schema
    validation — is what fires."""
    raw = yaml.safe_load(_WALKER_PATH.read_text(encoding="utf-8"))
    raw["routing"] = {
        "domains": [
            {
                "id": "bgp_domain",
                "protocol": "bgp",
                "selectors": [{"any": [{"segment": "leo"}, {"segment": "ground"}]}],
            }
        ]
    }
    response = client.post("/api/v1/builder/resolve-world", json={"document": raw})
    assert response.status_code == 422
    body = response.json()
    assert "runtime-unsupported" in body["error"]
    features = body["features"]
    assert features, "typed features did not ride the envelope"
    assert features[0]["category"] == "routing_protocol"
    assert features[0]["value"] == "bgp"
    assert features[0]["message"]


def test_session_form_refusal_ships_structured_envelope(monkeypatch, tmp_path):
    """A saved session that no longer resolves is a refused session, not an
    invalid request: the session form must ship the same typed envelope the
    document form does, never a bare 'request is invalid' string."""
    import vs_api.main as main

    raw = yaml.safe_load(_WALKER_PATH.read_text(encoding="utf-8"))
    raw["routing"] = {
        "domains": [
            {
                "id": "bgp_domain",
                "protocol": "bgp",
                "selectors": [{"any": [{"segment": "leo"}, {"segment": "ground"}]}],
            }
        ]
    }
    gated = tmp_path / "gated.yaml"
    gated.write_text(yaml.safe_dump(raw), encoding="utf-8")

    class _GatedSessionManager:
        def _validated_session_path(self, session_path: str) -> Path | None:
            return gated if session_path == "generated/gated.yaml" else None

    monkeypatch.setattr(main, "_session_manager", _GatedSessionManager())
    response = client.post(
        "/api/v1/builder/resolve-world", json={"session": "generated/gated.yaml"}
    )
    assert response.status_code == 422
    body = response.json()
    assert "runtime-unsupported" in body["error"]
    assert body["features"][0]["value"] == "bgp"


def test_save_session_resolves_then_writes_canonical_yaml(monkeypatch, tmp_path):
    import vs_api.main as main

    monkeypatch.setattr(main, "_generated_sessions_dir", lambda: tmp_path)
    monkeypatch.setattr(main, "_session_manager", None)
    raw = yaml.safe_load(_WALKER_PATH.read_text(encoding="utf-8"))
    response = client.post("/api/v1/builder/save-session", json={"document": raw})
    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "earth-leo-walker"
    assert payload["nodes"] == 181
    saved = list(tmp_path.glob("_builder-earth-leo-walker.yaml"))
    assert len(saved) == 1
    # The saved file is the canonical serialization and still resolves.
    saved_raw = yaml.safe_load(saved[0].read_text(encoding="utf-8"))
    assert saved_raw == raw
    assert len(resolve_session(saved_raw).nodes) == 181
    # One artifact per name: saving the same session again replaces the
    # file — no hash-suffixed siblings nobody can tell apart.
    again = client.post("/api/v1/builder/save-session", json={"document": raw})
    assert again.status_code == 200
    assert list(tmp_path.glob("_builder-*.yaml")) == saved


def test_save_session_rejects_unresolvable_document(monkeypatch, tmp_path):
    import vs_api.main as main

    monkeypatch.setattr(main, "_generated_sessions_dir", lambda: tmp_path)
    monkeypatch.setattr(main, "_session_manager", None)
    response = client.post(
        "/api/v1/builder/save-session",
        json={"document": {"session": {"name": "broken"}, "segments": []}},
    )
    assert response.status_code == 422
    assert response.json()["error"]
    assert list(tmp_path.glob("*.yaml")) == []


def test_save_session_requires_mapping_document():
    response = client.post("/api/v1/builder/save-session", json={"document": "nope"})
    assert response.status_code == 400


_USER_TERMINAL = {
    "terminal": {
        "id": "my-ka-terminal",
        "display_name": "My Ka terminal",
        "medium": "rf",
        "signal": {"band": "ka", "frequency_hz": 29.5e9},
        "bandwidth_mbps": {"transmit": 500.0, "receive": 500.0},
        "tracking_capacity": 1,
        "max_range_km": 2500.0,
        "limits": {
            "azimuth_deg": {"min": -180, "max": 180},
            "elevation_deg": {"min": 20, "max": 90},
            "max_tracking_rate_deg_s": 2.0,
        },
        "reference": "test",
    }
}


@pytest.fixture()
def user_roots(monkeypatch, tmp_path):
    """Builder endpoints see a tmp user catalog beside the real shipped one."""
    import vs_api.main as main
    from nodalarc.catalog_paths import CatalogRoots

    roots = CatalogRoots(
        root=main._CATALOG_ROOTS.root,
        sessions=main._CATALOG_ROOTS.sessions,
        user_root=tmp_path / "user-catalog",
    )
    monkeypatch.setattr(main, "_builder_catalog_roots", lambda: roots)
    monkeypatch.setattr(main, "_generated_sessions_dir", lambda: tmp_path / "generated")
    monkeypatch.setattr(main, "_session_manager", None)
    return roots


def test_user_catalog_save_browse_delete_round_trip(user_roots):
    saved = client.post(
        "/api/v1/builder/catalog/save",
        json={"family": "terminals", "document": _USER_TERMINAL},
    )
    assert saved.status_code == 200
    ref = saved.json()["ref"]
    assert ref == "user:terminals/my-ka-terminal.yaml"

    listed = client.get("/api/v1/builder/catalog", params={"family": "terminals"})
    refs = [e["ref"] for e in listed.json()]
    assert ref in refs
    assert any(r.startswith("nodalarc:") for r in refs)

    duplicate = client.post(
        "/api/v1/builder/catalog/save",
        json={"family": "terminals", "document": _USER_TERMINAL},
    )
    assert duplicate.status_code == 409

    overwritten = client.post(
        "/api/v1/builder/catalog/save",
        json={"family": "terminals", "document": _USER_TERMINAL, "overwrite": True},
    )
    assert overwritten.status_code == 200

    deleted = client.delete("/api/v1/builder/catalog/object", params={"ref": ref})
    assert deleted.status_code == 200
    assert ref not in [
        e["ref"]
        for e in client.get("/api/v1/builder/catalog", params={"family": "terminals"}).json()
    ]


def test_user_catalog_rejects_invalid_and_shipped_targets(user_roots):
    invalid = client.post(
        "/api/v1/builder/catalog/save",
        json={"family": "terminals", "document": {"terminal": {"id": "broken"}}},
    )
    assert invalid.status_code == 422

    shipped = client.delete(
        "/api/v1/builder/catalog/object",
        params={"ref": "nodalarc:terminals/rf/rf-ka-starlink-space-gateway.yaml"},
    )
    assert shipped.status_code == 400

    missing = client.delete(
        "/api/v1/builder/catalog/object",
        params={"ref": "user:terminals/never-existed.yaml"},
    )
    assert missing.status_code == 404


def test_save_session_flattens_user_references(user_roots):
    node_doc = {
        "node": {
            "id": "my-router",
            "display_name": "My router",
            "forwarding": "routed",
            "ethernet": [],
            "terminals": [
                {
                    "id": "access_ka",
                    "role": "access",
                    "terminal": "user:terminals/my-ka-terminal.yaml",
                    "count": 1,
                }
            ],
            "payloads": [],
        }
    }
    assert (
        client.post(
            "/api/v1/builder/catalog/save",
            json={"family": "terminals", "document": _USER_TERMINAL},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/builder/catalog/save",
            json={"family": "nodes", "document": node_doc},
        ).status_code
        == 200
    )

    raw = yaml.safe_load(_WALKER_PATH.read_text(encoding="utf-8"))
    # The probe node is access-only; the walker's isl rule would rightly fail
    # with zero compatible mounts, so the probe session drops it.
    raw["link_rules"] = [rule for rule in raw["link_rules"] if rule["id"] != "leo_isl"]
    raw["segments"][0]["source"] = {
        "constellation": {
            "id": "flatten-probe",
            "display_name": "Flatten probe",
            "node": "user:nodes/my-router.yaml",
            "orbit": "nodalarc:orbits/earth/leo/earth-leo-starlink.yaml",
            "planes": {"count": 1, "raan_spacing_deg": 0},
            "slots_per_plane": 2,
            "phasing": {"mode": "evenly_spaced_mean_anomaly", "phase_offset_deg": 0},
            "node_tags": [{"tag": "all"}],
            "reference": "test",
        }
    }
    response = client.post("/api/v1/builder/save-session", json={"document": raw})
    assert response.status_code == 200, response.json()
    saved = next((user_roots.user_root.parent / "generated").glob("_builder-*.yaml"))
    text = saved.read_text(encoding="utf-8")
    # Hermetic: no user references survive; the node is inline; shipped refs stay.
    assert "user:" not in text
    assert "my-ka-terminal" in text
    assert "nodalarc:orbits/earth/leo/earth-leo-starlink.yaml" in text
    assert len(resolve_session(yaml.safe_load(text)).nodes) > 0


def test_catalog_yaml_import_derives_family_and_export_round_trips(user_roots):
    imported = client.post(
        "/api/v1/builder/catalog/save",
        json={"document_yaml": yaml.dump(_USER_TERMINAL)},
    )
    assert imported.status_code == 200, imported.json()
    ref = imported.json()["ref"]
    assert ref == "user:terminals/my-ka-terminal.yaml"

    exported = client.get("/api/v1/builder/catalog/export", params={"ref": ref})
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/yaml")
    round_tripped = yaml.safe_load(exported.text)
    assert round_tripped["terminal"]["id"] == "my-ka-terminal"
    # Import of the export is identity (canonical both ways).
    again = client.post(
        "/api/v1/builder/catalog/save",
        json={"document_yaml": exported.text, "overwrite": True},
    )
    assert again.status_code == 200


def test_catalog_yaml_import_rejects_broken_yaml(user_roots):
    response = client.post(
        "/api/v1/builder/catalog/save",
        json={"document_yaml": "terminal: [unclosed"},
    )
    assert response.status_code == 422
    assert "invalid YAML" in response.json()["error"]


def test_segment_validation_refusal_ships_the_owning_segment():
    """A catalog-validation failure inside one segment's expansion is
    addressed mail: the wall carries that segment's id, never bare pydantic
    prose with no owner (an empty ground segment used to blank the whole
    builder with an unrouted error)."""
    raw = yaml.safe_load(_WALKER_PATH.read_text(encoding="utf-8"))
    raw["segments"].append(
        {
            "id": "empty-ground",
            "placement": {
                "from_site_set": {
                    "site_set": {
                        "id": "empty-sites",
                        "display_name": "Empty sites",
                        "sites": [],
                    }
                }
            },
        }
    )
    response = client.post("/api/v1/builder/resolve-world", json={"document": raw})
    assert response.status_code == 422
    body = response.json()
    assert body["subject"] == {"kind": "segment", "id": "empty-ground"}
    assert body["segment_id"] == "empty-ground"


def test_overlapping_domains_wall_is_summarized_and_addressed():
    """The disjointness refusal names the domains and a few example nodes —
    never every member — and its subject is the last declared overlapping
    domain (the one whose membership to fix)."""
    raw = yaml.safe_load(_WALKER_PATH.read_text(encoding="utf-8"))
    raw["routing"] = {
        "domains": [
            {
                "id": "everything",
                "protocol": "isis",
                "selectors": [{"any": [{"segment": "leo"}, {"segment": "ground"}]}],
                "area_assignment": {"strategy": "flat"},
            },
            {
                "id": "second_domain",
                "protocol": "isis",
                "selectors": [{"segment": "leo"}],
                "area_assignment": {"strategy": "flat"},
            },
        ]
    }
    response = client.post("/api/v1/builder/resolve-world", json={"document": raw})
    assert response.status_code == 422
    body = response.json()
    assert body["subject"] == {"kind": "routing_domain", "id": "second_domain"}
    assert "must be disjoint" in body["error"]
    assert "176 nodes" in body["error"]
    # Summarized: a handful of examples, not the whole membership.
    assert body["error"].count("sat-") <= 3
