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

import hashlib
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient
from nodalarc.catalog_paths import CatalogPathError
from nodalarc.models.builder_world import BuilderWorld
from nodalarc.models.events import EphemerisNodeFixed, EphemerisNodeKeplerian
from nodalarc.resolve_session import SessionResolutionError, resolve_session
from ome.builder_world import (
    _canonical_pairs,
    _closed_pair_count,
    _computed_preview,
    _preview_scope,
    build_builder_resolve_check,
    build_builder_save_artifact,
    build_builder_world,
    deploy_readiness_for_source,
)
from vs_api.main import app

_WALKER_REF = "nodalarc:sessions/earth-leo-walker.yaml"
_WALKER_PATH = Path("catalog/nodalarc/sessions/earth-leo-walker.yaml")

client = TestClient(app)


@pytest.fixture(scope="module")
def walker_world() -> BuilderWorld:
    return build_builder_world(_WALKER_REF)


def _ground_only_document() -> dict:
    """A grammar-valid session with a ground segment and no satellites.

    It resolves — a ground-only session is authorable grammar — but the
    preview world build refuses it, because the OME requires at least one
    satellite node. This is the fixture that exercises the save/preview
    boundary before satellite-less guard exists.
    """
    raw = dict(yaml.safe_load(_WALKER_PATH.read_text(encoding="utf-8")))
    raw["session"] = {**raw["session"], "name": "earth-leo-ground-only"}
    raw["segments"] = [seg for seg in raw["segments"] if seg["id"] == "ground"]
    raw.pop("link_rules", None)
    raw.pop("simulation", None)
    return raw


def test_save_artifact_saves_a_session_whose_preview_world_refuses():
    """save depends only on grammar validity. A ground-only session
    produces a save artifact — canonical bytes, hash, name, and node count from
    the resolved session, never a built world.
    """
    document = _ground_only_document()
    # the grammar-only save path succeeds.
    artifact = build_builder_save_artifact(document)
    assert artifact.session_name == "earth-leo-ground-only"
    assert artifact.node_count >= 1  # the gateway ground nodes
    assert artifact.document_yaml.strip()
    assert len(artifact.artifact_sha256) == 64


def test_satellite_less_session_renders_instead_of_walling():
    """satellite-less guard: a grammar-valid ground-only session is not a
    grammar error — it RESOLVES into a world with the ground nodes present,
    POPULATED body frames (so render scale anchors on a body) and empty node
    ephemerides (nothing to propagate), never a satellite-precondition wall. It
    still fails the deploy gate — that is runtime readiness, tested apart."""
    world = build_builder_world(_ground_only_document())
    assert world.nodes  # the ground gateways survive
    assert all(node.kind != "satellite" for node in world.nodes)
    # Body frames populated (render scale needs a body); no node ephemerides.
    assert "earth" in world.ephemeris.body_frames
    assert world.ephemeris.body_frames["earth"].equatorial_radius_km > 6000
    assert world.ephemeris.nodes == {}
    # Any surviving rule carries an accurate non-computed scope, never a wall.
    assert all(
        preview.preview_scope in ("terrestrial_pending", "inter_body_pending", "disabled")
        for preview in world.rule_previews
    )


def test_deploy_readiness_fact_gates_on_runtime_readiness():
    """a resolvable session may still be unable to start on the cluster.

    The resolve check ships deploy_ready + deploy_blockers computed from the
    operator's own readiness validator — a valid satellite-bearing session is
    ready; a ground-only session is not, and its blockers name BOTH the
    missing satellites AND a readiness error (E-code), proving the fact runs
    the full validator, not just the satellite count.
    """
    walker = build_builder_resolve_check(_WALKER_REF)
    assert walker.deploy_ready is True
    assert walker.deploy_blockers == ()

    ready, blockers = deploy_readiness_for_source(_ground_only_document())
    assert ready is False
    assert any("no satellites" in b for b in blockers)
    assert any(b.startswith("[E") for b in blockers)


def _switch_manager(key: str, path: Path):
    class _SM:
        status = "idle"

        def rescan(self) -> None:
            pass

        def _valid_session_files(self) -> dict[str, str]:
            return {key: str(path)}

        def _validated_session_path(self, requested: str) -> Path | None:
            return path if requested == key else None

    return _SM()


def test_switch_refuses_a_session_that_cannot_start(monkeypatch, tmp_path):
    """the switch endpoint refuses a non-runnable session BEFORE any CR
    mutation — a ground-only session never reaches the switch that would delete
    the running ConstellationSpec CR, so the running session stays up."""
    import vs_api.main as main

    ground_only = tmp_path / "ground-only.yaml"
    ground_only.write_text(yaml.safe_dump(_ground_only_document()), encoding="utf-8")
    key = "catalog/nodalarc/sessions/ground-only.yaml"
    monkeypatch.setattr(main, "_session_manager", _switch_manager(key, ground_only))

    switch_calls: list[str] = []

    async def _fake_run_switch(path: str) -> None:
        switch_calls.append(path)

    monkeypatch.setattr(main, "_run_switch", _fake_run_switch)

    response = client.post("/api/v1/sessions/switch", json={"session": key})
    assert response.status_code == 400
    assert "no satellites" in response.json()["error"]
    # The running session is never torn down: the switch was never scheduled.
    assert switch_calls == []


def test_switch_accepts_a_runnable_session(monkeypatch):
    """The guard lets a valid, satellite-bearing session through to the switch."""
    import vs_api.main as main

    key = "catalog/nodalarc/sessions/earth-leo-walker.yaml"
    monkeypatch.setattr(main, "_session_manager", _switch_manager(key, _WALKER_PATH))
    monkeypatch.setattr(main, "_run_switch", lambda path: _noop())

    response = client.post("/api/v1/sessions/switch", json={"session": key})
    assert response.status_code == 200
    assert response.json()["status"] == "switching"


async def _noop() -> None:
    pass


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


# --- server-computed rule previews --------------------------------------

_PREVIEW_VOCAB = {
    "los_blocked",
    "range_exceeded",
    "elevation_below_min",
    "field_of_regard",
    "no_geometry",
}


def _preview_node(node_id: str, kind: str, body: str) -> SimpleNamespace:
    return SimpleNamespace(
        node_id=node_id,
        kind=kind,
        central_body=body if kind == "satellite" else None,
        reference_body=body if kind != "satellite" else None,
    )


def _preview_rule(mode: str, ep0, ep1, *, enabled: bool = True, kind: str = "access"):
    endpoint = lambda ids: SimpleNamespace(node_ids=tuple(ids))  # noqa: E731
    return SimpleNamespace(
        rule_id="r",
        kind=kind,
        enabled=enabled,
        endpoints=(endpoint(ep0), endpoint(ep1)),
        topology=SimpleNamespace(mode=mode, n=None),
        constraints=None,
    )


def test_preview_scope_reads_body_kind_enabled_only():
    """Scope is decided from body/kind/enabled facts, no OME: disabled wins;
    no satellite endpoint is terrestrial; endpoints on different bodies are
    inter-body; same body with a satellite is computed."""
    nodes = {
        n.node_id: n
        for n in (
            _preview_node("s1", "satellite", "earth"),
            _preview_node("g1", "ground_station", "earth"),
            _preview_node("s2", "satellite", "luna"),
        )
    }
    assert (
        _preview_scope(_preview_rule("visible_candidates", ["g1"], ["s1"], enabled=False), nodes)
        == "disabled"
    )
    assert (
        _preview_scope(_preview_rule("visible_candidates", ["g1"], ["g1"]), nodes)
        == "terrestrial_pending"
    )
    assert (
        _preview_scope(_preview_rule("visible_candidates", ["g1"], ["s2"]), nodes)
        == "inter_body_pending"
    )
    assert _preview_scope(_preview_rule("visible_candidates", ["g1"], ["s1"]), nodes) == "computed"


def test_closed_pair_count_matches_the_canonical_generator():
    """The closed formula and the lazy generator must agree exactly — otherwise
    pairs_total (the formula) and the tested walk (the generator) diverge and
    the capped math lies."""
    for a, b in [
        (("a", "b", "c"), ("a", "b", "c")),  # self-mesh
        (("a", "b"), ("c", "d", "e")),  # disjoint
        (("a", "b", "c"), ("b", "c", "d")),  # partial overlap
        (("a",), ("a",)),  # single self -> zero pairs
        (("a", "b", "c", "d"), ("c", "d", "e", "f")),  # larger overlap
    ]:
        assert _closed_pair_count(a, b) == len(list(_canonical_pairs(a, b)))


def test_orientation_mismatch_fails_closed():
    """An allocated pair matching neither endpoint orientation is an engine
    inconsistency — the builder refuses on the wall channel, never normalizes
    it into a preview with zero lines."""
    rule = _preview_rule("explicit_pairs", ["A"], ["B"], kind="isl")
    nodes = {
        n.node_id: n
        for n in (
            _preview_node("A", "satellite", "earth"),
            _preview_node("B", "satellite", "earth"),
            _preview_node("C", "satellite", "earth"),
        )
    }
    candidate = SimpleNamespace(
        rule_id="r", node_a="A", node_b="C", interface_a="isl0", interface_b="isl0"
    )
    with pytest.raises(SessionResolutionError) as exc:
        _computed_preview(rule, nodes, {}, SimpleNamespace(), {}, {"r": [candidate]})
    assert exc.value.subject_id == "r"


def test_isl_limits_key_terminals_by_node_never_by_orientation():
    """Regression: a reverse-oriented fixed ISL pair must read each node against
    ITS OWN allocated interface. Keying by argument position instead re-pairs a
    node with the other's terminal and corrupts the range/FoR verdict."""
    from ome.builder_world import _isl_limits

    constraints = {
        "sat-a": {"isl0": SimpleNamespace(max_range_km=8000.0, field_of_regard_deg=360.0)},
        "sat-b": {"isl1": SimpleNamespace(max_range_km=1500.0, field_of_regard_deg=100.0)},
    }
    ctx = SimpleNamespace(sat_isl_terminal_constraints=constraints)
    candidate = SimpleNamespace(
        node_a="sat-a", node_b="sat-b", interface_a="isl0", interface_b="isl1"
    )
    rule = _preview_rule("explicit_pairs", ["sat-a"], ["sat-b"], kind="isl")
    forward = _isl_limits("sat-a", "sat-b", candidate, ctx, rule)
    reverse = _isl_limits("sat-b", "sat-a", candidate, ctx, rule)
    # min(8000, 1500) range, min(360, 100) FoR — identical whichever way round.
    assert forward == (1500.0, 100.0)
    assert reverse == forward


def test_isl_verdict_gates_incompatible_terminal_types():
    """The runtime refuses an ISL pair between incompatible terminal types
    before any geometry; the preview reports terminal_type_mismatch (a VERDICT
    reason, never a resolve refusal) and draws no line, rather than a
    false-positive candidate. Matching types are covered by the walker's ISL
    rule, whose same-segment terminals all match and all draw."""
    from ome.builder_world import _isl_verdict

    # Positions are never read: the type gate fires before geometry.
    state = SimpleNamespace(position_ecef_km=None, velocity_ecef_km_s=None, geodetic=None)
    ctx = SimpleNamespace(
        body_frames={"earth": object()},
        sat_isl_terminal_constraints={
            "sat-a": {
                "isl0": SimpleNamespace(
                    terminal_type="optical-lct-a", max_range_km=5000.0, field_of_regard_deg=360.0
                )
            },
            "sat-b": {
                "isl1": SimpleNamespace(
                    terminal_type="optical-lct-b", max_range_km=5000.0, field_of_regard_deg=360.0
                )
            },
        },
    )
    candidate = SimpleNamespace(
        node_a="sat-a", node_b="sat-b", interface_a="isl0", interface_b="isl1"
    )
    rule = _preview_rule("explicit_pairs", ["sat-a"], ["sat-b"], kind="isl")
    verdict = _isl_verdict(
        "sat-a", "sat-b", candidate, {"sat-a": state, "sat-b": state}, ctx, rule, "earth"
    )
    # A verdict string, never a raised refusal — the session stays authorable.
    assert verdict == "terminal_type_mismatch"


def test_walker_previews_cover_every_rule(walker_world):
    ids = {p.rule_id for p in walker_world.rule_previews}
    assert ids == {rule.rule_id for rule in walker_world.link_rules}
    assert all(p.preview_scope == "computed" for p in walker_world.rule_previews)


def test_walker_preview_counts_reconcile_over_tested(walker_world):
    """Every tested pair is drawn or accounted for by exactly one reason —
    counts sum over pairs_tested, the honest denominator, and drawn pairs are
    real nodes."""
    node_ids = {node.node_id for node in walker_world.nodes}
    computed = [p for p in walker_world.rule_previews if p.preview_scope == "computed"]
    assert computed
    for preview in computed:
        reason_sum = sum(rc.count for rc in preview.reason_counts)
        # The walker is below the draw cap, so drawn == passing and the sum is tight.
        assert reason_sum + preview.pairs_drawn == preview.pairs_tested
        assert preview.pairs_drawn == len(preview.drawable_pairs)
        assert preview.pairs_tested == min(preview.pairs_total, 4000)
        assert not preview.capped
        assert all(rc.reason in _PREVIEW_VOCAB for rc in preview.reason_counts)
        for pair in preview.drawable_pairs:
            assert pair.node_a in node_ids and pair.node_b in node_ids
            assert pair.rule_id == preview.rule_id


def test_walker_gate_set_matches_the_frozen_epoch_contract(walker_world):
    """Only the gates the runtime would enforce at this epoch appear. Ground
    access under geometry_only carries no boresight/range, and the motion gates
    are off, so ground reasons are LOS/elevation only; ISL adds range and FoR
    but never tracking_exceeded or polar_seam."""
    by_id = {p.rule_id: p for p in walker_world.rule_previews}
    access = by_id["leo_access"]
    isl = by_id["leo_isl"]
    assert {rc.reason for rc in access.reason_counts} <= {"los_blocked", "elevation_below_min"}
    assert {rc.reason for rc in isl.reason_counts} <= {
        "los_blocked",
        "range_exceeded",
        "field_of_regard",
    }


def test_walker_fixed_isl_draws_the_allocated_pairs(walker_world):
    """The fixed ISL rule's preview universe is EXACTLY the allocator's resolved
    pair count (the surviving capacity fact), drawn one-for-one when feasible."""
    isl = next(p for p in walker_world.rule_previews if p.rule_id == "leo_isl")
    allocated = next(a for a in walker_world.allocations if a.rule_id == "leo_isl")
    assert isl.pairs_total == allocated.allocated_pairs
    # Every allocated ISL pair is feasible at the epoch, so drawn == total.
    assert isl.pairs_drawn == isl.pairs_total


def test_preview_budget_caps_deterministically(monkeypatch):
    """When the universe exceeds the tested budget the preview is an honest
    partial: total is the full closed-form universe, tested is the budget,
    capped is true, and reason counts sum over TESTED — never the universe."""
    import ome.builder_world as builder_world_module

    monkeypatch.setattr(builder_world_module, "_PREVIEW_TESTED_BUDGET", 50)
    world = build_builder_world(_WALKER_REF)
    access = next(p for p in world.rule_previews if p.rule_id == "leo_access")
    assert access.pairs_total == 704  # the universe is unchanged (closed formula)
    assert access.pairs_tested == 50  # geometry ran over the budget only
    assert access.capped is True
    assert sum(rc.count for rc in access.reason_counts) + access.pairs_drawn == 50


def test_disabled_rule_ships_no_geometry(walker_world):
    """A rule the user switched off shows its wall and nothing else — no pairs,
    no reason counts — never a false state display for a rule that is off."""
    raw = dict(yaml.safe_load(_WALKER_PATH.read_text(encoding="utf-8")))
    raw["session"] = {**raw["session"], "name": "earth-leo-walker-disabled"}
    raw["link_rules"] = [
        {**rule, "enabled": False} if rule["id"] == "leo_access" else rule
        for rule in raw["link_rules"]
    ]
    world = build_builder_world(raw)
    access = next(p for p in world.rule_previews if p.rule_id == "leo_access")
    assert access.preview_scope == "disabled"
    assert access.pairs_total == 0
    assert access.pairs_tested == 0
    assert access.pairs_drawn == 0
    assert access.reason_counts == ()
    assert access.drawable_pairs == ()


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
    # The allocator's pair count reconciles against the preview universe (the
    # link_candidates wire field is gone; rule_previews.pairs_total is the same
    # candidate universe, per rule).
    previews = {p["rule_id"]: p for p in world["rule_previews"]}
    for rule_id, alloc in allocations.items():
        assert alloc["allocated_pairs"] == previews[rule_id]["pairs_total"]
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


def test_save_session_refuses_stem_collision_with_a_different_session(monkeypatch, tmp_path):
    import vs_api.main as main

    monkeypatch.setattr(main, "_generated_sessions_dir", lambda: tmp_path)
    monkeypatch.setattr(main, "_session_manager", None)
    raw = yaml.safe_load(_WALKER_PATH.read_text(encoding="utf-8"))  # name: earth-leo-walker
    # A DIFFERENT session already occupies this name's file (distinct names that
    # normalize to one lossy stem land here). Saving must refuse, never clobber.
    occupied = tmp_path / "_builder-earth-leo-walker.yaml"
    occupied.write_text("session:\n  name: someone-elses-session\nsegments: []\n")
    response = client.post("/api/v1/builder/save-session", json={"document": raw})
    assert response.status_code == 409
    assert "collides" in response.json()["error"].lower()
    assert "someone-elses-session" in occupied.read_text()  # the other file is untouched
    # Re-saving over a file that holds the SAME name still replaces (no false collision).
    occupied.write_text("session:\n  name: earth-leo-walker\nsegments: []\n")
    assert client.post("/api/v1/builder/save-session", json={"document": raw}).status_code == 200


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


def test_save_artifact_hash_is_the_written_bytes(monkeypatch, tmp_path):
    import vs_api.main as main

    monkeypatch.setattr(main, "_generated_sessions_dir", lambda: tmp_path)
    monkeypatch.setattr(main, "_session_manager", None)
    raw = yaml.safe_load(_WALKER_PATH.read_text(encoding="utf-8"))
    payload = client.post("/api/v1/builder/save-session", json={"document": raw}).json()
    saved = tmp_path / "_builder-earth-leo-walker.yaml"
    assert payload["artifact_sha256"] == hashlib.sha256(saved.read_bytes()).hexdigest()


def test_resolve_artifact_hash_is_the_hypothetical_save(monkeypatch, tmp_path):
    # The resolve check predicts exactly what a save of the same document
    # writes — the deploy gate compares these two ends.
    import vs_api.main as main

    monkeypatch.setattr(main, "_generated_sessions_dir", lambda: tmp_path)
    monkeypatch.setattr(main, "_session_manager", None)
    raw = yaml.safe_load(_WALKER_PATH.read_text(encoding="utf-8"))
    resolved = client.post("/api/v1/builder/resolve-world", json={"document": raw}).json()
    saved = client.post("/api/v1/builder/save-session", json={"document": raw}).json()
    assert resolved["artifact_sha256"] == saved["artifact_sha256"]


def _user_probe_session() -> dict:
    """Walker variant whose constellation node is a user-library reference."""
    raw = yaml.safe_load(_WALKER_PATH.read_text(encoding="utf-8"))
    raw["link_rules"] = [rule for rule in raw["link_rules"] if rule["id"] != "leo_isl"]
    raw["segments"][0]["source"] = {
        "constellation": {
            "id": "hash-probe",
            "display_name": "Hash probe",
            "node": "user:nodes/my-router.yaml",
            "orbit": "nodalarc:orbits/earth/leo/earth-leo-starlink.yaml",
            "planes": {"count": 1, "raan_spacing_deg": 0},
            "slots_per_plane": 2,
            "phasing": {"mode": "evenly_spaced_mean_anomaly", "phase_offset_deg": 0},
            "node_tags": [{"tag": "all"}],
            "reference": "test",
        }
    }
    return raw


_USER_PROBE_NODE = {
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


def _save_user_probe_objects() -> None:
    for family, document in (("terminals", _USER_TERMINAL), ("nodes", _USER_PROBE_NODE)):
        response = client.post(
            "/api/v1/builder/catalog/save", json={"family": family, "document": document}
        )
        assert response.status_code == 200, response.json()


def test_referenced_user_object_change_alters_artifact_hash(user_roots):
    _save_user_probe_objects()
    raw = _user_probe_session()
    before = client.post("/api/v1/builder/resolve-world", json={"document": raw}).json()

    changed = {"terminal": {**_USER_TERMINAL["terminal"], "max_range_km": 3000.0}}
    assert (
        client.post(
            "/api/v1/builder/catalog/save",
            json={"family": "terminals", "document": changed, "overwrite": True},
        ).status_code
        == 200
    )
    after = client.post("/api/v1/builder/resolve-world", json={"document": raw}).json()
    assert after["artifact_sha256"] != before["artifact_sha256"]


def test_unrelated_user_object_change_keeps_artifact_hash(user_roots):
    _save_user_probe_objects()
    raw = _user_probe_session()
    before = client.post("/api/v1/builder/resolve-world", json={"document": raw}).json()

    unrelated = {"terminal": {**_USER_TERMINAL["terminal"], "id": "my-other-terminal"}}
    assert (
        client.post(
            "/api/v1/builder/catalog/save", json={"family": "terminals", "document": unrelated}
        ).status_code
        == 200
    )
    after = client.post("/api/v1/builder/resolve-world", json={"document": raw}).json()
    assert after["artifact_sha256"] == before["artifact_sha256"]


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


def test_inline_lunar_surface_site_resolves():
    """The builder emits authored ground as inline site objects; a lunar
    site inlined that way must resolve exactly like the shipped
    by-reference form — ground authoring is body-parameterized, never
    Earth-assumed."""
    raw = yaml.safe_load(
        Path("catalog/nodalarc/sessions/earth-leo-heo-geo-luna-reachability.yaml").read_text(
            encoding="utf-8"
        )
    )
    artemis = yaml.safe_load(
        Path("catalog/nodalarc/sites/luna/luna-artemis-base.yaml").read_text(encoding="utf-8")
    )
    luna_ground = next(s for s in raw["segments"] if s["id"] == "luna_ground")
    luna_ground["placement"]["from_site_set"] = {
        "site_set": {
            "id": "authored-luna-sites",
            "display_name": "Authored luna sites",
            "sites": [{"site": artemis["site"]}],
            "reference": "session-builder-draft",
        }
    }
    response = client.post("/api/v1/builder/resolve-world", json={"document": raw})
    assert response.status_code == 200, response.json().get("error")
    world = response.json()["world"]
    luna_nodes = [
        n
        for n in world["nodes"]
        if n["segment_id"] == "luna_ground" and n["surface_position"] is not None
    ]
    assert luna_nodes, "no lunar ground node resolved"
    assert luna_nodes[0]["surface_position"]["body"] == "luna"
    assert luna_nodes[0]["surface_position"]["lat_deg"] == -89.4
