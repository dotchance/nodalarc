# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Access links between a ground station and an orbiter of another body, and the refusal of a
direct link between the surfaces of two bodies.

The fixture forks the shipped lunar DTN relay into a bus with two access terminals: the shipped
S-band mount for lunar stations and a second mount for Earth stations. Each access rule selects
its terminal by mount, so the bus serves stations on both bodies at once.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from nodalarc.catalog_closure import FilesystemCatalogReadView
from nodalarc.catalog_paths import CatalogRoots
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.models.terminal_physics import SatGroundTerminalBoresight
from nodalarc.resolve_session import SessionResolutionError, resolve_session
from nodalarc.runtime_support import UnsupportedFeatureError

from tests.catalog_session_fixtures import SHIPPED_ROOT

RELAY = "luna-relay-sat-p00s00"
SESSION = SHIPPED_ROOT / "sessions" / "earth-luna-dtn.yaml"


def _load(path: Path) -> dict[str, Any]:
    return load_configuration_yaml(path.read_bytes())


def _write(root: Path, relative: str, document: dict[str, Any]) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


@pytest.fixture
def catalog(tmp_path: Path) -> FilesystemCatalogReadView:
    """The shipped catalog plus a user: relay bus with an Earth-facing access terminal."""
    node = _load(SHIPPED_ROOT / "nodes" / "space" / "luna-dtn-relay-vehicle.yaml")
    node["node"]["id"] = "luna-relay-two-faces"
    node["node"]["terminals"].insert(
        1,
        {
            "id": "access_earth",
            "role": "access",
            "terminal": "nodalarc:terminals/rf/rf-ka-geo-access.yaml",
            "count": 1,
            "boresight": {"mode": "nadir"},
        },
    )
    _write(tmp_path, "nodes/space/luna-relay-two-faces.yaml", node)
    node_set = _load(SHIPPED_ROOT / "space-node-sets" / "luna" / "luna-dtn-relay-1.yaml")
    node_set["space_node_set"]["id"] = "luna-relay-two-faces-1"
    node_set["space_node_set"]["nodes"][0]["node"] = "user:nodes/space/luna-relay-two-faces.yaml"
    _write(tmp_path, "space-node-sets/luna/luna-relay-two-faces-1.yaml", node_set)
    return FilesystemCatalogReadView(CatalogRoots(root=SHIPPED_ROOT, user_root=tmp_path))


def _rule(raw: dict[str, Any], rule_id: str) -> dict[str, Any]:
    return next(rule for rule in raw["link_rules"] if rule["id"] == rule_id)


def _by_mount(mount: str) -> dict[str, Any]:
    return {"all": [{"role": "access"}, {"mount": mount}]}


def _two_face_session() -> dict[str, Any]:
    """earth-luna-dtn on the two-terminal relay: the lunar access rules select the S-band mount,
    and the Earth DTN station's rule selects the relay's Earth-facing mount."""
    raw = _load(SESSION)
    segment = next(seg for seg in raw["segments"] if seg["id"] == "luna_relay")
    segment["source"] = "user:space-node-sets/luna/luna-relay-two-faces-1.yaml"
    for rule_id in ("luna_access", "dtn_luna_access"):
        _rule(raw, rule_id)["endpoints"][1]["terminal"] = _by_mount("access_s")
    earth = _rule(raw, "dtn_earth_access")
    earth["endpoints"][1]["select"] = {"segment": "luna_relay"}
    earth["endpoints"][1]["terminal"] = _by_mount("access_earth")
    return raw


def test_each_access_terminal_points_at_the_body_of_the_stations_it_serves(
    catalog: FilesystemCatalogReadView,
) -> None:
    resolved = resolve_session(_two_face_session(), catalog=catalog)

    relay = resolved.node_by_id(RELAY)
    assert relay is not None
    aimed = {
        block.terminal_id: block.boresight.target_body
        for block in relay.terminal_inventory
        if isinstance(block.boresight, SatGroundTerminalBoresight)
    }
    assert aimed == {"access_s": "luna", "access_earth": "earth"}
    earth_stations = {
        node.node_id
        for node in resolved.nodes
        if node.kind == "ground_station" and node.reference_body == "earth"
    }
    cross_body = [
        candidate
        for candidate in resolved.link_candidates
        if candidate.kind == "access"
        and RELAY in candidate.pair
        and set(candidate.pair) & earth_stations
    ]
    assert cross_body, "no access candidate joins an Earth station and the lunar relay"


def test_one_terminal_paired_with_stations_on_two_bodies_is_refused(
    catalog: FilesystemCatalogReadView,
) -> None:
    raw = _two_face_session()
    _rule(raw, "dtn_earth_access")["endpoints"][1]["terminal"] = _by_mount("access_s")

    with pytest.raises(SessionResolutionError) as refusal:
        resolve_session(raw, catalog=catalog)

    message = str(refusal.value)
    assert f"satellite {RELAY!r} access terminal 'access_s'" in message
    assert "earth through dtn_earth_access" in message
    assert "luna through dtn_luna_access, luna_access" in message


def test_a_direct_link_between_the_surfaces_of_two_bodies_is_unsupported() -> None:
    raw = _load(SESSION)
    lunar = _rule(raw, "luna_access")
    lunar["endpoints"][1] = {
        "select": {"all": [{"segment": "dtn_earth_ground"}, {"tag": "leo"}]},
        "terminal": lunar["endpoints"][1]["terminal"],
    }
    raw["link_rules"].remove(_rule(raw, "dtn_earth_access"))

    with pytest.raises(UnsupportedFeatureError) as refusal:
        resolve_session(
            raw, catalog=FilesystemCatalogReadView(CatalogRoots.from_catalog_root(SHIPPED_ROOT))
        )

    (feature,) = refusal.value.features
    assert (feature.category, feature.value) == ("link_topology", "surface_to_surface_cross_body")
    assert "link rule 'luna_access' joins ground stations on earth and luna" in feature.message


def test_ome_evaluates_an_earth_station_and_the_lunar_relay_at_their_true_range(
    catalog: FilesystemCatalogReadView,
) -> None:
    """One OME tick of the two-terminal session: every Earth station and lunar relay candidate is
    decided, with a range of Earth-Moon size measured in the common frame, and is rejected for
    range by the Earth station's 3,600 km access terminal."""
    from nodalarc.models.session import session_epoch_unix
    from nodalarc.ome_inputs import build_ome_inputs_from_resolved
    from ome.event_stream import build_step_context, compute_step

    resolved = resolve_session(_two_face_session(), catalog=catalog)
    inputs = build_ome_inputs_from_resolved(resolved)
    ctx = build_step_context(
        satellites=inputs.satellites,
        addressing=inputs.addressing,
        gs_file=inputs.gs_file,
        neighbors=inputs.neighbors,
        propagator_id=inputs.propagator_id,
        ground_scheduling=inputs.ground_scheduling,
        ground_link_model=inputs.ground_link_model,
        ground_candidate_satellites_by_gs=inputs.ground_candidate_satellites_by_gs,
        node_metadata=inputs.node_metadata,
        body_frames=inputs.body_frames,
        physics=inputs.physics,
        active_bodies=inputs.active_bodies,
    )
    result = compute_step(
        ctx,
        session_epoch_unix(resolved.time),
        step=0,
        step_seconds=float(resolved.time.step_seconds),
        timestamp_offset=0.0,
        isl_state={},
        gs_state={},
        current_associations={},
    )

    decisions = {
        pair: decision
        for pair, decision in result.ground_decisions.items()
        if RELAY in pair and decision.reference_body == "earth"
    }
    assert decisions
    for pair, decision in decisions.items():
        assert 350_000.0 < decision.range_km < 410_000.0, pair
        assert not decision.visible
        assert decision.reject_reason in {"range_exceeded", "los_blocked", "elevation_below_min"}


def test_the_builder_previews_an_earth_station_rule_to_the_lunar_relay(
    catalog: FilesystemCatalogReadView,
) -> None:
    """The Builder judges the cross-body access rule with the runtime's own signals and checks:
    it computes the rule, every tested pair has a verdict, and the counts add up."""
    from nodalarc.resolve_session import resolve_session_with_assets
    from vs_api.builder_world import world_from_resolution

    world = world_from_resolution(resolve_session_with_assets(_two_face_session(), catalog=catalog))

    preview = next(item for item in world.rule_previews if item.rule_id == "dtn_earth_access")
    assert preview.preview_scope == "computed"
    assert preview.pairs_tested > 0
    judged = preview.pairs_drawn + sum(count.count for count in preview.reason_counts)
    assert judged == preview.pairs_tested
