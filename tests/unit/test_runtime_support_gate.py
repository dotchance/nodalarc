# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The runtime-support gate is mandatory at resolve time.

Production never passes an explicit RuntimeSupport, so the resolver default
(Earth-Luna) must run the typed UnsupportedFeature layer unconditionally:
grammar-valid-but-unimplemented features fail at resolution — at upload or
deploy — never as untyped errors after a pod is already running.
"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

import pytest
from nodalarc.resolve_session import SessionResolutionError, resolve_session
from nodalarc.runtime_support import FeatureCategory, UnsupportedFeatureError

from tests.conftest import build_segment_session_dict


def _session(**kwargs) -> dict:
    defaults = {
        "name": "runtime-support-gate",
        "constellation": {"planes": {"count": 1, "sats_per_plane": 2}},
        "ground_stations": {"stations": ["a"]},
    }
    defaults.update(kwargs)
    return build_segment_session_dict(**defaults)


def _luna_body() -> dict:
    return {
        "body": {
            "id": "luna",
            "display_name": "Luna",
            "gravitational_parameter_km3_s2": 4902.800066,
            "mean_radius_km": 1737.4,
            "equatorial_radius_km": 1738.1,
            "polar_radius_km": 1736.0,
            "reference": "test-fixture",
        }
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _de440s_manifest(*, sha: str | None = None, coverage_end: str = "2026-07-01T00:00:00Z") -> dict:
    kernel_path = Path("configs/ephemerides/de440s.bsp")
    kernel = {
        "id": "de440s",
        "path": str(kernel_path),
        "targets": [_luna_body()],
        "frame": "gcrs",
        "coverage_start": "2026-06-01T00:00:00Z",
        "coverage_end": coverage_end,
        "sha256": sha if sha is not None else _sha256(kernel_path),
    }
    return {"provider": "skyfield_bsp", "quality_tier": "de440s", "kernels": [kernel]}


def _lunar_constellation(raw: dict) -> dict:
    orbit = raw["segments"][0]["source"]["constellation"]["orbit"]["orbit"]
    orbit["central_body"] = _luna_body()
    orbit["id"] = "luna-low-test"
    orbit["shape"] = {"altitude_km": 100}
    orbit["orientation"]["inclination_deg"] = 90
    return raw


class TestMandatoryGate:
    def test_bgp_routing_domain_rejected_typed_without_explicit_support(self) -> None:
        raw = _session(protocol="bgp")
        with pytest.raises(UnsupportedFeatureError) as err:
            resolve_session(raw)
        cats = {f.category for f in err.value.features}
        assert FeatureCategory.ROUTING_PROTOCOL in cats
        assert any(f.value == "bgp" for f in err.value.features)

    def test_unsupported_ephemeris_provider_rejected_typed_by_default(self) -> None:
        raw = _lunar_constellation(_session())
        raw["ephemeris"] = _de440s_manifest()
        raw["ephemeris"]["provider"] = "spice_kernel_stack"
        with pytest.raises(UnsupportedFeatureError) as err:
            resolve_session(raw)
        assert any(
            f.category == FeatureCategory.EPHEMERIS_PROVIDER and f.value == "spice_kernel_stack"
            for f in err.value.features
        )

    def test_static_routing_domain_is_supported(self) -> None:
        raw = _session(protocol="static")
        resolved = resolve_session(raw)
        assert {d.protocol for d in resolved.routing_domains} == {"static"}

    def test_luna_session_passes_default_earth_luna_gate(self) -> None:
        raw = _lunar_constellation(_session())
        for site in raw["segments"][1]["placement"]["from_site_set"]["site_set"]["sites"]:
            site["site"]["frame"]["body_fixed"]["body"] = _luna_body()
            site["site"]["location"] = {"lat_deg": -80.0, "lon_deg": 0.0, "alt_m": 0.0}
        raw["ephemeris"] = _de440s_manifest()
        resolved = resolve_session(raw)
        assert {n.central_body for n in resolved.nodes if n.kind == "satellite"} == {"luna"}


class TestCrossBodyAccess:
    def test_earth_ground_to_luna_satellite_access_rejected_at_resolve(self) -> None:
        # Ground sites stay on Earth; the constellation moves to Luna; the
        # default access rule pairs them — which must be rejected as
        # cross-body, never evaluated as mixed-frame geometry.
        raw = _lunar_constellation(_session())
        raw["ephemeris"] = _de440s_manifest()
        with pytest.raises(SessionResolutionError, match="body-local"):
            resolve_session(raw)


class TestEphemerisManifestAtResolve:
    def test_checksum_mismatch_fails_at_resolve(self) -> None:
        raw = _lunar_constellation(_session())
        for site in raw["segments"][1]["placement"]["from_site_set"]["site_set"]["sites"]:
            site["site"]["frame"]["body_fixed"]["body"] = _luna_body()
            site["site"]["location"] = {"lat_deg": -80.0, "lon_deg": 0.0, "alt_m": 0.0}
        raw["ephemeris"] = _de440s_manifest(sha="0" * 64)
        with pytest.raises(SessionResolutionError, match="checksum mismatch"):
            resolve_session(raw)

    def test_stale_coverage_fails_at_resolve(self) -> None:
        raw = _lunar_constellation(_session())
        for site in raw["segments"][1]["placement"]["from_site_set"]["site_set"]["sites"]:
            site["site"]["frame"]["body_fixed"]["body"] = _luna_body()
            site["site"]["location"] = {"lat_deg": -80.0, "lon_deg": 0.0, "alt_m": 0.0}
        manifest = _de440s_manifest(coverage_end="2026-06-02T00:00:00Z")
        manifest["kernels"][0]["coverage_start"] = "2026-06-01T00:00:00Z"
        raw["ephemeris"] = manifest
        # Session epoch is 2026-06-08 — outside the declared coverage window.
        with pytest.raises(SessionResolutionError, match="does not cover"):
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
        assert any("by_attach_index" in f.value for f in err.value.features)

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

    def test_node_payloads_rejected_typed(self) -> None:
        raw = _session()
        node = raw["segments"][0]["source"]["constellation"]["node"]["node"]
        node["payloads"] = [
            {
                "id": "cam-mount",
                "count": 1,
                "payload": {
                    "payload": {
                        "id": "cam",
                        "terminal_slots": [],
                    }
                },
            }
        ]
        with pytest.raises(UnsupportedFeatureError) as err:
            resolve_session(raw)
        assert any(f.category == FeatureCategory.PAYLOAD for f in err.value.features)

    def test_max_links_per_node_is_enforced_at_resolve(self) -> None:
        raw = _session(constellation={"planes": {"count": 1, "sats_per_plane": 4}})
        raw["link_rules"][1]["topology"] = {"mode": "nearest_n", "n": 2}
        raw["link_rules"][1]["constraints"] = {"max_links_per_node": 1}
        with pytest.raises(SessionResolutionError, match="exceeding max_links_per_node"):
            resolve_session(raw)

    def test_unconsumed_dynamic_constraints_rejected_loudly(self) -> None:
        raw = _session()
        raw["link_rules"][1]["constraints"] = {"max_range_km": 4000.0}
        with pytest.raises(SessionResolutionError, match="unsupported runtime constraint"):
            resolve_session(raw)

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

    def test_inert_orbit_default_propagator_rejected(self) -> None:
        raw = _session()
        raw["orbit"] = {"default_propagator": "j2_mean_elements"}
        with pytest.raises(SessionResolutionError, match="inert"):
            resolve_session(raw)

    def test_unknown_terminal_install_mount_rejected(self) -> None:
        raw = _session()
        sites = raw["segments"][1]["placement"]["from_site_set"]["site_set"]["sites"]
        site_node = sites[0]["site"]["nodes"][0]
        site_node["terminals"]["no-such-mount"] = {"installed_count": 1}
        with pytest.raises(SessionResolutionError, match="unknown mount"):
            resolve_session(raw)

    def test_allocator_wide_scheduling_divergence_rejected_at_resolve(self) -> None:
        raw = _session(ground_stations={"stations": ["a", "b"]})
        sites = raw["segments"][1]["placement"]["from_site_set"]["site_set"]["sites"]
        sites[0]["site"]["nodes"][0]["scheduling"] = {
            "ranking_order": ["selection_score", "lex_pair"]
        }
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
        node = raw["segments"][0]["source"]["constellation"]["node"]["node"]
        # Two ISL mounts with different mediums; manifest order puts the rf
        # mount's interface first. The rf terminal definition is cloned from
        # the (rf) access terminal so it stays a valid rf primitive.
        optical_mount = next(mount for mount in node["terminals"] if mount["role"] == "isl")
        access_mount = next(mount for mount in node["terminals"] if mount["role"] == "access")
        rf_isl = {
            "id": "isl_rf",
            "role": "isl",
            "count": optical_mount["count"],
            "terminal": deepcopy(access_mount["terminal"]),
        }
        rf_isl["terminal"]["terminal"]["id"] = "isl-rf-test"
        node["terminals"].insert(0, rf_isl)

        resolved = resolve_session(raw)
        sat = next(n for n in resolved.nodes if n.kind == "satellite")
        name_by_terminal = {w.name: w.terminal_id for w in sat.wan_interfaces}
        optical_candidates = [c for c in resolved.link_candidates if c.terminal_medium == "optical"]
        assert optical_candidates
        for candidate in optical_candidates:
            for node_id, iface in (
                (candidate.node_a, candidate.interface_a),
                (candidate.node_b, candidate.interface_b),
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
