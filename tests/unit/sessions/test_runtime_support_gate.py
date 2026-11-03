# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The runtime-support gate is mandatory at resolve time.

Production never passes an explicit RuntimeSupport, so the resolver default
(Earth-Luna) must run the typed UnsupportedFeature layer unconditionally:
grammar-valid-but-unimplemented features fail at resolution — at upload or
deploy — never as untyped errors after a pod is already running.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from nodalarc.catalog_closure import FilesystemCatalogReadView
from nodalarc.catalog_paths import CatalogRoots
from nodalarc.catalog_refs import CatalogRef
from nodalarc.physics import EphemerisLocation
from nodalarc.physics import context as physics_context
from nodalarc.physics.datasets import production_location
from nodalarc.resolve_session import SessionResolutionError
from nodalarc.runtime_support import FeatureCategory, RuntimeSupport, UnsupportedFeatureError

from tests.catalog_session_fixtures import (
    CatalogSessionFixture,
    build_catalog_session_fixture,
)
from tests.catalog_session_fixtures import (
    resolve_catalog_session as resolve_session,
)

ROOT = Path(__file__).resolve().parents[3]
SHIPPED_ROOT = ROOT / "catalog" / "nodalarc"


def _session(**kwargs) -> dict:
    defaults = {
        "name": "runtime-support-gate",
        "constellation": {"planes": {"count": 1, "sats_per_plane": 2}},
        "ground_stations": {"stations": ["a"]},
    }
    defaults.update(kwargs)
    return build_catalog_session_fixture(**defaults)


def _explicit_node_clock_session(
    tmp_path: Path,
    clock: dict,
) -> tuple[dict, CatalogRoots, str]:
    user_root = tmp_path / "user"
    source_path = SHIPPED_ROOT / "space-node-sets" / "earth" / "geo" / "earth-geo-tdrs-6.yaml"
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    source["space_node_set"]["id"] = "runtime-clock-nodes"
    source["space_node_set"]["nodes"][0]["clock"] = clock
    target = user_root / "space-node-sets" / "runtime-clock-nodes.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(source, sort_keys=False), encoding="utf-8")

    session_path = SHIPPED_ROOT / "sessions" / "earth-geo-tdrs.yaml"
    session = yaml.safe_load(session_path.read_text(encoding="utf-8"))
    session["segments"][0]["source"] = "user:space-node-sets/runtime-clock-nodes.yaml"
    roots = CatalogRoots.from_catalog_root(SHIPPED_ROOT, user_root=user_root)
    return session, roots, source["space_node_set"]["nodes"][0]["id"]


def _lunar_constellation(raw: CatalogSessionFixture) -> CatalogSessionFixture:
    assert raw.orbit_ref is not None
    orbit_document = raw.read_catalog(raw.orbit_ref)
    orbit = orbit_document["orbit"]
    orbit["central_body"] = "nodalarc:bodies/luna.yaml"
    orbit["id"] = "luna-low-test"
    orbit["shape"] = {"altitude_km": 100}
    orbit["orientation"]["inclination_deg"] = 90
    orbit["id"] = raw.orbit_ref.relative_path.stem
    raw.write_catalog(raw.orbit_ref, orbit_document)
    return raw


def _move_ground_sites_to_luna(raw: CatalogSessionFixture) -> None:
    for site_ref in raw.site_refs:
        site_document = raw.read_catalog(site_ref)
        site = site_document["site"]
        site["frame"]["body_fixed"]["body"] = "nodalarc:bodies/luna.yaml"
        site["location"] = {"lat_deg": -80.0, "lon_deg": 0.0, "alt_m": 0.0}
        raw.write_catalog(site_ref, site_document)


class TestMandatoryGate:
    def test_bgp_routing_domain_rejected_typed_without_explicit_support(self) -> None:
        raw = _session(protocol="bgp")
        with pytest.raises(UnsupportedFeatureError) as err:
            resolve_session(raw)
        cats = {f.category for f in err.value.features}
        assert FeatureCategory.ROUTING_PROTOCOL in cats
        assert any(f.value == "bgp" for f in err.value.features)

    def test_static_routing_domain_is_supported(self) -> None:
        raw = _session(protocol="static")
        resolved = resolve_session(raw)
        assert {d.protocol for d in resolved.routing_domains} == {"static"}

    def test_implemented_igp_capabilities_remain_supported(self) -> None:
        raw = _session(protocol="isis", extensions=["mpls", "sr", "te"])

        resolved = resolve_session(raw)

        assert resolved.routing_domains[0].capabilities == (
            "mpls",
            "segment_routing",
            "traffic_engineering",
        )

    def test_luna_session_passes_default_earth_luna_gate(self) -> None:
        raw = _lunar_constellation(_session())
        _move_ground_sites_to_luna(raw)
        resolved = resolve_session(raw)
        assert {n.central_body for n in resolved.nodes if n.kind == "satellite"} == {"luna"}
        # The platform selects the data for the session's bodies.
        assert [f.file_id for f in resolved.ephemeris_files] == [
            "de440s",
            "moon-orientation",
            "moon-frames",
        ]

    def test_earth_only_profile_refuses_luna_orbits_and_sites_at_their_objects(self) -> None:
        raw = _lunar_constellation(_session())
        _move_ground_sites_to_luna(raw)
        with pytest.raises(UnsupportedFeatureError) as refusal:
            resolve_session(raw, runtime_support=RuntimeSupport.earth_multi_regime())
        # The orbit's central body and the site's frame body are each the
        # object's own gate, the one the Builder's component window runs.
        found = {(feature.category, feature.value) for feature in refusal.value.features}
        assert (FeatureCategory.CENTRAL_BODY, "luna") in found
        assert (FeatureCategory.REFERENCE_BODY, "luna") in found


class TestCrossBodyAccess:
    def test_earth_ground_to_luna_satellite_access_rejected_at_resolve(self) -> None:
        # Ground sites stay on Earth; the constellation moves to Luna; the
        # default access rule pairs them — which must be rejected as
        # cross-body, never evaluated as mixed-frame geometry.
        raw = _lunar_constellation(_session())
        with pytest.raises(SessionResolutionError, match="body-local"):
            resolve_session(raw)


class TestEphemerisDataAtResolve:
    def test_session_outside_the_data_coverage_fails_at_resolve(self) -> None:
        raw = _lunar_constellation(_session())
        _move_ground_sites_to_luna(raw)
        # The planetary positions file ends in January 2150.
        raw["time"]["start_time"] = "2160-01-01T00:00:00Z"
        with pytest.raises(SessionResolutionError, match="does not cover"):
            resolve_session(raw)

    def test_data_file_that_does_not_match_its_pin_fails_at_resolve(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real = production_location()
        source = tmp_path / "ephemerides"
        source.mkdir()
        for item in real.source_dir.iterdir():
            if item.is_file() and item.name != "moon_pa_de440_200625.bpc":
                (source / item.name).symlink_to(item)
        (source / "moon_pa_de440_200625.bpc").write_bytes(b"not the pinned file")
        injected = EphemerisLocation(source_dir=source, converted_dir=real.converted_dir)
        monkeypatch.setattr(physics_context, "production_location", lambda: injected)
        raw = _lunar_constellation(_session())
        _move_ground_sites_to_luna(raw)
        with pytest.raises(SessionResolutionError, match="does not match its pin"):
            resolve_session(raw)


class TestUnconsumedGrammarClass:
    """Every grammar field is consumed or rejected typed — these prove the
    rejected dispositions actually fire (the completeness contract only
    proves they are classified)."""

    def test_point_to_point_pool_rejected_typed(self) -> None:
        raw = _session()
        raw["addressing"]["point_to_point"] = [
            {
                "id": "p2p",
                "applies_to": {"segment": "space"},
                "ipv4_pool": "10.128.0.0/12",
                "prefix_length": 31,
            }
        ]
        with pytest.raises(UnsupportedFeatureError) as err:
            resolve_session(raw)
        assert any(f.value == "point_to_point" for f in err.value.features)

    def test_unimplemented_allocation_strategy_rejected_typed(self) -> None:
        raw = _session()
        raw["addressing"]["loopbacks"][0]["allocation"] = "by_attach_index"
        with pytest.raises(UnsupportedFeatureError) as err:
            resolve_session(raw)
        assert any(
            f.category == FeatureCategory.ADDRESS_ALLOCATION and f.value == "by_attach_index"
            for f in err.value.features
        )

    def test_nearest_visible_topology_rejected_typed(self) -> None:
        raw = _session()
        raw["link_rules"][1]["topology"] = {"mode": "nearest_visible"}
        with pytest.raises(UnsupportedFeatureError) as err:
            resolve_session(raw)
        assert any(
            feature.category == FeatureCategory.LINK_TOPOLOGY and feature.value == "nearest_visible"
            for feature in err.value.features
        )

    def test_affine_segment_clock_rejected_typed(self) -> None:
        raw = _session()
        raw["segments"][0]["clock"] = {"model": "affine", "rate": 2.0}
        with pytest.raises(UnsupportedFeatureError) as err:
            resolve_session(raw)
        assert any(
            f.category == FeatureCategory.CLOCK_MODEL and f.value == "affine"
            for f in err.value.features
        )

    def test_session_clock_threads_onto_resolved_nodes(self) -> None:
        raw = _session()
        raw["segments"][0]["clock"] = {"model": "session"}
        resolved = resolve_session(raw)
        sats = [n for n in resolved.nodes if n.kind == "satellite"]
        assert sats and all(n.clock.model == "session" for n in sats)

    def test_affine_explicit_node_clock_rejected_typed(self, tmp_path: Path) -> None:
        raw, roots, _ = _explicit_node_clock_session(
            tmp_path,
            {"model": "affine", "rate": 2.0},
        )
        with pytest.raises(UnsupportedFeatureError) as err:
            resolve_session(raw, catalog=FilesystemCatalogReadView(roots))
        assert any(
            feature.category == FeatureCategory.CLOCK_MODEL and feature.value == "affine"
            for feature in err.value.features
        )

    def test_session_explicit_node_clock_threads_to_resolved_node(self, tmp_path: Path) -> None:
        raw, roots, local_node_id = _explicit_node_clock_session(
            tmp_path,
            {"model": "session"},
        )

        resolved = resolve_session(raw, catalog=FilesystemCatalogReadView(roots))
        node = next(item for item in resolved.nodes if item.local_node_id == local_node_id)

        assert node.clock.model == "session"

    def test_node_payloads_rejected_typed_under_a_narrowed_profile(self) -> None:
        # Onboard execution is production-supported; the boundary mechanism
        # must still refuse typed for a runtime profile without it.
        narrowed = RuntimeSupport.earth_luna().model_copy(update={"supports_payloads": False})
        raw = _session()
        assert raw.space_node_ref is not None
        node_document = raw.read_catalog(raw.space_node_ref)
        node = node_document["node"]
        payload_ref = CatalogRef("user:payloads/runtime-support-camera.yaml")
        raw.create_catalog(
            payload_ref,
            {
                "payload": {
                    "id": payload_ref.relative_path.stem,
                    "forwarding": "host",
                    "profile": "nodalarc:profiles/linux-host.yaml",
                }
            },
        )
        node["ethernet"] = [{"id": "bus0"}]
        node["payloads"] = [
            {
                "id": "cam-mount",
                "count": 1,
                "payload": str(payload_ref),
                "attach": "bus0",
            }
        ]
        raw.write_catalog(raw.space_node_ref, node_document)
        with pytest.raises(UnsupportedFeatureError) as err:
            resolve_session(raw, runtime_support=narrowed)
        assert any(f.category == FeatureCategory.PAYLOAD for f in err.value.features)

    def test_max_links_per_node_is_enforced_at_resolve(self) -> None:
        raw = _session(constellation={"planes": {"count": 1, "sats_per_plane": 4}})
        raw["link_rules"][1]["topology"] = {"mode": "nearest_n", "n": 2}
        raw["link_rules"][1]["constraints"] = {"max_links_per_node": 1}
        with pytest.raises(SessionResolutionError, match="exceeding max_links_per_node"):
            resolve_session(raw)

    @pytest.mark.parametrize("constraint", ["max_range_km", "require_mutual_visibility"])
    def test_unconsumed_dynamic_constraints_rejected_typed(self, constraint: str) -> None:
        raw = _session()
        value = 4000.0 if constraint == "max_range_km" else True
        raw["link_rules"][1]["constraints"] = {constraint: value}
        with pytest.raises(UnsupportedFeatureError) as err:
            resolve_session(raw)
        assert any(
            feature.category == FeatureCategory.LINK_CONSTRAINT and feature.value == constraint
            for feature in err.value.features
        )

    def test_static_routing_capability_rejected_typed(self) -> None:
        raw = _session(protocol="static")
        raw["routing"]["domains"][0]["capabilities"] = {"mpls": {}}
        with pytest.raises(UnsupportedFeatureError) as err:
            resolve_session(raw)
        assert any(
            feature.category == FeatureCategory.ROUTING_CAPABILITY
            and feature.value == "static:mpls"
            for feature in err.value.features
        )

    def test_multi_segment_session_requires_candidate_limits(self) -> None:
        raw = _session()
        del raw["simulation"]
        with pytest.raises(SessionResolutionError, match="must declare"):
            resolve_session(raw)

    def test_declared_candidate_bound_fails_before_materialization(self) -> None:
        raw = _session(constellation={"planes": {"count": 2, "sats_per_plane": 4}})
        raw["simulation"]["candidate_limits"]["max_pairs_per_rule"] = 3
        with pytest.raises(SessionResolutionError, match="before materialization"):
            resolve_session(raw)

    def test_unknown_terminal_install_mount_rejected(self) -> None:
        raw = _session()
        site_document = raw.read_catalog(raw.site_refs[0])
        site_node = site_document["site"]["nodes"][0]
        site_node["terminals"]["no-such-mount"] = {"installed_count": 1}
        raw.write_catalog(raw.site_refs[0], site_document)
        with pytest.raises(SessionResolutionError, match="unknown mount"):
            resolve_session(raw)

    def test_allocator_wide_scheduling_divergence_rejected_at_resolve(self) -> None:
        raw = _session(ground_stations={"stations": ["a", "b"]})
        site_document = raw.read_catalog(raw.site_refs[0])
        site_document["site"]["nodes"][0]["scheduling"] = {
            "ranking_order": ["selection_score", "lex_pair"]
        }
        raw.write_catalog(raw.site_refs[0], site_document)
        with pytest.raises(SessionResolutionError, match="allocator-wide"):
            resolve_session(raw)


class TestTerminalBindingClass:
    """Terminal compatibility is authored, never inferred: one ground terminal
    binds to exactly one access rule, and fixed-link candidate interfaces
    belong to the terminal block the rule actually selected."""

    def test_ground_terminal_bound_by_two_access_rules_rejected(self) -> None:
        raw = _session()
        # Second constellation + second access rule whose terminal selector
        # matches the same ground terminal pool.
        second = deepcopy(raw["segments"][0])
        second["id"] = "space_b"
        raw["segments"].append(second)
        raw["routing"]["domains"][0]["selectors"] = [
            {"any": [{"segment": "space"}, {"segment": "space_b"}, {"segment": "ground"}]}
        ]
        raw["link_rules"].append(
            {
                "id": "ground-access-b",
                "endpoints": [
                    {
                        "select": {"segment": "ground"},
                        "terminal": {"all": [{"role": "access"}, {"medium": "rf"}]},
                    },
                    {
                        "select": {"segment": "space_b"},
                        "terminal": {"all": [{"role": "access"}, {"medium": "rf"}]},
                    },
                ],
                "topology": {"mode": "visible_candidates"},
            }
        )
        with pytest.raises(SessionResolutionError, match="bindings must be disjoint"):
            resolve_session(raw)

    def test_fixed_interfaces_come_from_the_selected_terminal_block(self) -> None:
        raw = _session(constellation={"planes": {"count": 1, "sats_per_plane": 2}})
        assert raw.space_node_ref is not None
        node_document = raw.read_catalog(raw.space_node_ref)
        node = node_document["node"]
        # Two ISL mounts with different mediums; manifest order puts the rf
        # mount's interface first. The rf terminal definition is cloned from
        # the (rf) access terminal so it stays a valid rf primitive.
        optical_mount = next(mount for mount in node["terminals"] if mount["role"] == "isl")
        access_mount = next(mount for mount in node["terminals"] if mount["role"] == "access")
        rf_isl = {
            "id": "isl_rf",
            "role": "isl",
            "count": optical_mount["count"],
            "terminal": "user:terminals/isl-rf-test.yaml",
        }
        rf_terminal = raw.read_catalog(CatalogRef(access_mount["terminal"]))
        rf_terminal["terminal"]["id"] = "isl-rf-test"
        raw.create_catalog(CatalogRef(rf_isl["terminal"]), rf_terminal)
        node["terminals"].insert(0, rf_isl)
        raw.write_catalog(raw.space_node_ref, node_document)

        resolved = resolve_session(raw)
        sat = next(n for n in resolved.nodes if n.kind == "satellite")
        name_by_terminal = {w.name: w.terminal_id for w in sat.wan_interfaces}
        optical_candidates = [
            candidate
            for candidate in resolved.link_candidates
            if candidate.kind != "access" and candidate.terminal_medium == "optical"
        ]
        assert optical_candidates
        for candidate in optical_candidates:
            interface_a, interface_b = candidate.fixed_interfaces
            for node_id, iface in (
                (candidate.node_a, interface_a),
                (candidate.node_b, interface_b),
            ):
                owner = name_by_terminal[iface]
                block = next(
                    b
                    for n in resolved.nodes
                    if n.node_id == node_id
                    for b in n.terminal_inventory
                    if b.terminal_id == owner
                )
                assert block.medium == "optical", (
                    f"optical candidate claimed {iface} owned by {block.medium} mount"
                )


class TestSharedSitePlacement:
    """A site is a place; a place exists once. Placement groups are labels."""

    def _two_group_session(self, second_apply: dict | None = None) -> dict:
        raw = _session(ground_stations={"stations": ["a"]})
        ground = deepcopy(raw["segments"][1])
        ground["id"] = "ground_b"
        if second_apply is not None:
            ground["apply"] = second_apply
        raw["segments"].append(ground)
        # The duplicate group joins the existing access rule's ground side.
        raw["link_rules"][0]["endpoints"][0]["select"] = {
            "any": [{"segment": "ground"}, {"segment": "ground_b"}]
        }
        return raw

    def test_site_placed_by_two_groups_instantiates_once(self) -> None:
        raw = self._two_group_session()
        resolved = resolve_session(raw)
        ground_nodes = [n for n in resolved.nodes if n.kind == "ground_station"]
        assert len(ground_nodes) == 1
        node = ground_nodes[0]
        assert node.placement_groups == ("ground", "ground_b")
        assert node.segment_id == "ground"

    def test_conflicting_group_apply_for_shared_site_rejected(self) -> None:
        raw = self._two_group_session(
            second_apply={
                "scheduling": {
                    "selection_policy": {"lowest_elevation": {}},
                    "handover_policy": {
                        "hysteresis": {"discount_factor": 1.15, "mask_fade_range_deg": 5.0}
                    },
                    "handover_mode": "bbm",
                    "mbb_overlap_ticks": 0,
                    "mbb_reserve": 0,
                }
            }
        )
        with pytest.raises(SessionResolutionError, match="conflicting"):
            resolve_session(raw)

    def test_conflicting_group_clocks_for_shared_site_rejected(self) -> None:
        raw = self._two_group_session()
        raw["segments"][1]["clock"] = {"model": "affine", "rate": 2.0}
        support = RuntimeSupport.earth_luna().model_copy(
            update={"supported_clock_models": frozenset({"session", "affine"})}
        )

        with pytest.raises(SessionResolutionError, match="clock.*must be identical"):
            resolve_session(raw, runtime_support=support)
