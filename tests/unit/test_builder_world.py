"""Read-only resolved-world projection used by backend Builder compilation."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import yaml
from nodalarc.catalog_paths import CatalogPathError
from nodalarc.models.builder_world import BuilderWorld
from nodalarc.models.events import EphemerisNodeFixed, EphemerisNodeKeplerian, EphemerisNodeTLE
from nodalarc.resolve_session import resolve_session
from vs_api.builder_world import (
    _builder_rule_allocations,
    _canonical_pairs,
    _closed_pair_count,
    build_builder_world,
)

from tests.catalog_session_fixtures import (
    build_catalog_session_fixture,
    install_tle_space_node_set,
)

WALKER_REF = "nodalarc:sessions/earth-leo-walker.yaml"
WALKER_PATH = Path("catalog/nodalarc/sessions/earth-leo-walker.yaml")


@pytest.fixture(scope="module")
def walker_world() -> BuilderWorld:
    return build_builder_world(WALKER_REF)


def _ground_only_document() -> dict:
    raw = dict(yaml.safe_load(WALKER_PATH.read_text(encoding="utf-8")))
    raw["session"] = {**raw["session"], "name": "earth-leo-ground-only"}
    raw["segments"] = [segment for segment in raw["segments"] if segment["id"] == "ground"]
    raw.pop("link_rules", None)
    raw.pop("simulation", None)
    return raw


def test_world_node_set_and_kinds_match_resolver(walker_world: BuilderWorld) -> None:
    raw = yaml.safe_load(WALKER_PATH.read_text(encoding="utf-8"))
    resolved = resolve_session(raw)
    resolved_by_id = {node.node_id: node for node in resolved.nodes}

    assert {node.node_id for node in walker_world.nodes} == set(resolved_by_id)
    for node in walker_world.nodes:
        assert node.kind == resolved_by_id[node.node_id].kind
        ephemeris = walker_world.ephemeris.nodes.get(node.node_id)
        if node.kind == "satellite":
            assert isinstance(ephemeris, EphemerisNodeKeplerian)
        elif ephemeris is not None:
            assert isinstance(ephemeris, EphemerisNodeFixed)


def test_ground_only_session_remains_visible_in_preview() -> None:
    world = build_builder_world(_ground_only_document())

    assert world.nodes
    assert all(node.kind != "satellite" for node in world.nodes)
    assert "earth" in world.ephemeris.body_frames
    assert world.ephemeris.body_frames["earth"].equatorial_radius_km > 6_000
    assert world.ephemeris.nodes == {}


def test_world_projects_resolver_hardware_and_links(walker_world: BuilderWorld) -> None:
    assert walker_world.link_rules
    assert all(node.interfaces is not None for node in walker_world.nodes)
    assert any(node.terminal_inventory for node in walker_world.nodes)

    world_ids = {node.node_id for node in walker_world.nodes}
    for rule in walker_world.link_rules:
        assert len(rule.endpoints) == 2
        for endpoint in rule.endpoints:
            assert set(endpoint.node_ids) <= world_ids
            assert endpoint.segment_id
            assert endpoint.terminal_role


def test_allocator_projection_uses_resolved_truth_only() -> None:
    raw = yaml.safe_load(WALKER_PATH.read_text(encoding="utf-8"))
    resolved = resolve_session(raw)
    projected = _builder_rule_allocations(resolved)
    candidates_by_rule = {
        rule.rule_id: sum(
            candidate.rule_id == rule.rule_id for candidate in resolved.link_candidates
        )
        for rule in resolved.link_rules
    }

    assert {allocation.rule_id for allocation in projected} == set(candidates_by_rule)
    assert {
        allocation.rule_id: allocation.allocated_pairs for allocation in projected
    } == candidates_by_rule
    assert all(
        node.free == node.matching
        for allocation in projected
        if allocation.kind == "access"
        for node in allocation.per_node
    )


def test_preview_and_allocation_counts_reconcile(walker_world: BuilderWorld) -> None:
    allocations = {allocation.rule_id: allocation for allocation in walker_world.allocations}
    previews = {preview.rule_id: preview for preview in walker_world.rule_previews}

    assert allocations
    assert allocations.keys() <= previews.keys()
    for rule_id, allocation in allocations.items():
        preview = previews[rule_id]
        assert allocation.allocated_pairs == preview.pairs_total
        assert preview.pairs_drawn + sum(item.count for item in preview.reason_counts) == (
            preview.pairs_tested
        )
        assert preview.pairs_tested <= preview.pairs_total
        for per_node in allocation.per_node:
            assert 0 <= per_node.free <= per_node.matching


def test_fixed_isl_preview_draws_allocated_pairs(walker_world: BuilderWorld) -> None:
    allocation = next(item for item in walker_world.allocations if item.rule_id == "leo_isl")
    preview = next(item for item in walker_world.rule_previews if item.rule_id == "leo_isl")

    assert preview.preview_scope == "computed"
    assert preview.pairs_total == allocation.allocated_pairs
    assert preview.pairs_drawn == len(preview.drawable_pairs)
    assert all(pair.rule_id == "leo_isl" for pair in preview.drawable_pairs)


def test_closed_pair_count_matches_canonical_pair_generator() -> None:
    for left_count, right_count, same_side in (
        (1, 1, False),
        (3, 5, False),
        (4, 4, True),
        (0, 5, False),
    ):
        left = tuple(f"left-{index}" for index in range(left_count))
        right = left if same_side else tuple(f"right-{index}" for index in range(right_count))
        assert _closed_pair_count(left, right) == len(tuple(_canonical_pairs(left, right)))


def test_epoch_and_body_facts_come_from_session(walker_world: BuilderWorld) -> None:
    raw = yaml.safe_load(WALKER_PATH.read_text(encoding="utf-8"))
    expected = datetime.fromisoformat(raw["time"]["start_time"].replace("Z", "+00:00"))

    assert walker_world.epoch_unix == expected.timestamp()
    earth = walker_world.ephemeris.body_frames["earth"]
    assert earth.equatorial_radius_km > 6_000
    assert earth.mean_radius_km > 6_000


def test_tle_world_carries_ome_propagated_epoch_positions() -> None:
    session = build_catalog_session_fixture(
        name="builder-sgp4-preview",
        constellation={"planes": {"count": 1, "sats_per_plane": 2}},
        ground_stations={"stations": ["a"]},
    )
    install_tle_space_node_set(session)

    world = build_builder_world(dict(session), catalog_roots=session.roots)
    satellites = [node for node in world.nodes if node.kind == "satellite"]

    assert len(satellites) == 2
    assert all(
        isinstance(world.ephemeris.nodes[node.node_id], EphemerisNodeTLE) for node in satellites
    )
    assert all(node.epoch_position is not None for node in satellites)
    assert all(
        node.epoch_position is not None and node.epoch_position.alt_km > 0 for node in satellites
    )


def test_reference_containment_and_family_are_enforced() -> None:
    with pytest.raises(CatalogPathError):
        build_builder_world("nodalarc:../secrets.yaml")
    with pytest.raises(ValueError):
        build_builder_world("nodalarc:orbits/earth/leo/earth-leo-starlink.yaml")
