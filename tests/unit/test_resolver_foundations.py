# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Tests for the catalog resolver runtime contract."""

import pytest
from nodalarc.models.identity import IdentityMode
from nodalarc.models.link_rules import VisibleCandidatesTopology
from nodalarc.models.resolved_session import (
    ResolvedBodyFacts,
    ResolvedEndpoint,
    ResolvedInterfaceAddress,
    ResolvedLinkCandidate,
    ResolvedLinkRule,
    ResolvedNode,
    ResolvedNodeInterfaces,
    ResolvedOrbitFacts,
    ResolvedRoutingDomain,
    ResolvedSession,
    ResolvedSurfacePosition,
    ResolvedTerminalBlock,
    SidBlock,
    SourceContext,
)
from nodalarc.models.segment_session import SessionMeta
from nodalarc.models.segments import GroundScheduling
from nodalarc.runtime_support import (
    FeatureCategory,
    RuntimeSupport,
    UnsupportedFeature,
    UnsupportedFeatureError,
)
from pydantic import ValidationError


def _orbit() -> ResolvedOrbitFacts:
    return ResolvedOrbitFacts(
        orbit_id="earth-leo-test",
        central_body="earth",
        epoch="2026-06-08T00:00:00Z",
        propagator="j2_mean_elements",
        semi_major_axis_km=6978.137,
        eccentricity=0.0,
        inclination_deg=53.0,
        raan_deg=0.0,
        argument_of_perigee_deg=0.0,
        mean_anomaly_deg=0.0,
    )


def _surface() -> ResolvedSurfacePosition:
    return ResolvedSurfacePosition(body="earth", lat_deg=39.0, lon_deg=-104.0, alt_m=1800.0)


def _body() -> ResolvedBodyFacts:
    return ResolvedBodyFacts(
        body_id="earth",
        display_name="Earth",
        gravitational_parameter_km3_s2=398600.4418,
        mean_radius_km=6371.0088,
        equatorial_radius_km=6378.137,
        polar_radius_km=6356.752,
        reference="test:earth-body",
    )


def _interfaces() -> ResolvedNodeInterfaces:
    return ResolvedNodeInterfaces(
        lo0=ResolvedInterfaceAddress(ipv4="10.255.0.1/32", ipv6="fd00::1/128"),
        terr0=ResolvedInterfaceAddress(ipv4="172.16.1.1/24", ipv6="fd10::1/64"),
    )


def _terminal(
    node_id: str,
    *,
    terminal_id: str = "isl_optical",
    role: str = "isl",
    medium: str = "optical",
    count: int = 1,
) -> ResolvedTerminalBlock:
    return ResolvedTerminalBlock(
        terminal_id=terminal_id,
        owner_node_id=node_id,
        endpoint_role=role,
        medium=medium,
        count=count,
        tracking_capacity=1,
        max_range_km=5000.0,
        bandwidth_mbps=10000.0,
        source_ref=f"test:{terminal_id}",
    )


def _satellite(node_id: str = "leo-sat-p00s00", *, segment_id: str = "leo") -> ResolvedNode:
    return ResolvedNode(
        node_id=node_id,
        local_node_id=node_id.removeprefix(f"{segment_id}-"),
        segment_id=segment_id,
        namespace=segment_id,
        kind="satellite",
        frame_id="earth",
        central_body="earth",
        terminal_inventory=(_terminal(node_id),),
        orbit=_orbit(),
        forwarding="routed",
        interfaces=ResolvedNodeInterfaces(lo0=ResolvedInterfaceAddress(ipv4="10.255.0.1/32")),
        plane=0,
        slot=0,
    )


def _ground(node_id: str = "ground-denver-router", *, segment_id: str = "ground") -> ResolvedNode:
    return ResolvedNode(
        node_id=node_id,
        local_node_id="denver-router",
        segment_id=segment_id,
        namespace=segment_id,
        kind="ground_station",
        frame_id="earth",
        reference_body="earth",
        terminal_inventory=(
            _terminal(node_id, terminal_id="access_ka", role="access", medium="rf", count=2),
        ),
        interfaces=_interfaces(),
        surface_position=_surface(),
        forwarding="routed",
        ground_scheduling=GroundScheduling(
            selection_policy={"highest_elevation": {}},
            handover_policy={"hard_release": {}},
            handover_mode="bbm",
            mbb_overlap_ticks=0,
            mbb_reserve=0,
            handover_concurrency="one_at_a_time",
            ranking_order=("service_priority", "selection_score", "lex_pair"),
        ),
    )


def _domain(*node_ids: str, domain_id: str = "earth_domain") -> ResolvedRoutingDomain:
    return ResolvedRoutingDomain(
        domain_id=domain_id,
        protocol="isis",
        node_ids=tuple(node_ids),
        capabilities=("segment_routing",),
    )


def _sid(*node_ids: str, domain_id: str = "earth_domain", start: int = 16000) -> SidBlock:
    return SidBlock(
        domain_id=domain_id,
        node_ids=tuple(node_ids),
        sid_start=start,
        sid_end=start + len(node_ids) - 1,
    )


def _resolved_session(**overrides) -> ResolvedSession:
    nodes = overrides.pop("nodes", (_satellite(),))
    if "routing_domains" in overrides:
        domain = overrides.pop("routing_domains")
    else:
        domain = (_domain(*(node.node_id for node in nodes)),)
    if "sid_blocks" in overrides:
        sid_blocks = overrides.pop("sid_blocks")
    else:
        sid_blocks = tuple(
            _sid(*domain_item.node_ids, domain_id=domain_item.domain_id)
            for domain_item in domain
            if "segment_routing" in domain_item.capabilities
        )
    base = {
        "identity_mode": IdentityMode.SEGMENT_NAMESPACED,
        "session": SessionMeta(name="demo"),
        "nodes": nodes,
        "bodies": (_body(),),
        "link_rules": (),
        "link_candidates": (),
        "routing_domains": domain,
        "sid_blocks": sid_blocks,
        "source_context": SourceContext(origin="test"),
    }
    base.update(overrides)
    return ResolvedSession(**base)


def test_resolved_session_constructs_and_helpers() -> None:
    rs = _resolved_session()
    assert rs.identity_mode is IdentityMode.SEGMENT_NAMESPACED
    assert rs.node_ids() == ("leo-sat-p00s00",)
    assert rs.node_by_id("leo-sat-p00s00").local_node_id == "sat-p00s00"
    assert rs.node_by_id("missing") is None


def test_resolved_models_are_frozen() -> None:
    node = _satellite()
    with pytest.raises(ValidationError):
        node.node_id = "other"
    with pytest.raises(ValidationError):
        node.orbit.mean_anomaly_deg = 42.0
    rs = _resolved_session()
    with pytest.raises(ValidationError):
        rs.identity_mode = IdentityMode.SEGMENT_NAMESPACED


def test_closed_terminal_role_vocabulary_rejects_old_ground_role() -> None:
    with pytest.raises(ValidationError, match="access"):
        _terminal("ground-denver-router", role="ground")


def test_satellite_requires_orbit_facts() -> None:
    with pytest.raises(ValidationError, match="requires orbit facts"):
        ResolvedNode(
            node_id="sat",
            local_node_id="sat",
            segment_id="leo",
            namespace="leo",
            kind="satellite",
            frame_id="earth",
            central_body="earth",
            terminal_inventory=(_terminal("sat"),),
        )


def test_ground_station_requires_surface_position_and_scheduling() -> None:
    with pytest.raises(ValidationError, match="ground_scheduling"):
        ResolvedNode(
            node_id="gs",
            local_node_id="gs",
            segment_id="ground",
            namespace="ground",
            kind="ground_station",
            frame_id="earth",
            reference_body="earth",
            terminal_inventory=(_terminal("gs", role="access", medium="rf"),),
            interfaces=_interfaces(),
            surface_position=_surface(),
        )
    with pytest.raises(ValidationError, match="surface_position"):
        ResolvedNode(
            node_id="gs",
            local_node_id="gs",
            segment_id="ground",
            namespace="ground",
            kind="ground_station",
            frame_id="earth",
            reference_body="earth",
            terminal_inventory=(_terminal("gs", role="access", medium="rf"),),
            interfaces=_interfaces(),
            ground_scheduling=GroundScheduling(),
        )


def test_duplicate_node_id_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate runtime node_id"):
        _resolved_session(
            nodes=(_satellite("dup"), _satellite("dup")),
            routing_domains=(),
            sid_blocks=(),
        )


def test_terminal_owner_mismatch_rejected() -> None:
    block = _terminal("other-node")
    with pytest.raises(ValidationError, match="owner_node_id"):
        ResolvedNode(
            node_id="n",
            local_node_id="n",
            segment_id="leo",
            namespace="leo",
            kind="satellite",
            frame_id="earth",
            central_body="earth",
            terminal_inventory=(block,),
            orbit=_orbit(),
        )


def test_duplicate_terminal_id_within_node_rejected() -> None:
    block = _terminal("n", terminal_id="dup")
    with pytest.raises(ValidationError, match="duplicate terminal_id"):
        ResolvedNode(
            node_id="n",
            local_node_id="n",
            segment_id="leo",
            namespace="leo",
            kind="satellite",
            frame_id="earth",
            central_body="earth",
            terminal_inventory=(block, block),
            orbit=_orbit(),
        )


def test_endpoint_rejects_empty_or_duplicate_node_ids() -> None:
    with pytest.raises(ValidationError):
        ResolvedEndpoint(segment_id="leo", terminal_role="isl", node_ids=())
    with pytest.raises(ValidationError, match="duplicate node_id"):
        ResolvedEndpoint(segment_id="leo", terminal_role="isl", node_ids=("sat", "sat"))


def test_link_rule_endpoint_referencing_unknown_node_rejected() -> None:
    rule = ResolvedLinkRule(
        rule_id="r",
        kind="access",
        enabled=True,
        endpoints=(
            ResolvedEndpoint(segment_id="ground", terminal_role="access", node_ids=("ghost",)),
            ResolvedEndpoint(
                segment_id="leo", terminal_role="access", node_ids=("leo-sat-p00s00",)
            ),
        ),
        topology=VisibleCandidatesTopology(mode="visible_candidates"),
    )
    with pytest.raises(ValidationError, match="unknown"):
        _resolved_session(link_rules=(rule,))


def test_endpoint_cross_segment_membership_rejected() -> None:
    rule = ResolvedLinkRule(
        rule_id="r",
        kind="isl",
        enabled=True,
        endpoints=(
            ResolvedEndpoint(segment_id="other", terminal_role="isl", node_ids=("leo-sat-p00s00",)),
            ResolvedEndpoint(segment_id="leo", terminal_role="isl", node_ids=("leo-sat-p00s01",)),
        ),
        topology=VisibleCandidatesTopology(mode="visible_candidates"),
    )
    with pytest.raises(ValidationError, match="another"):
        _resolved_session(
            nodes=(_satellite("leo-sat-p00s00"), _satellite("leo-sat-p00s01")),
            link_rules=(rule,),
        )


def test_duplicate_link_rule_id_rejected() -> None:
    rule = ResolvedLinkRule(
        rule_id="r",
        kind="isl",
        enabled=True,
        endpoints=(
            ResolvedEndpoint(segment_id="leo", terminal_role="isl", node_ids=("leo-sat-p00s00",)),
            ResolvedEndpoint(segment_id="leo", terminal_role="isl", node_ids=("leo-sat-p00s01",)),
        ),
        topology=VisibleCandidatesTopology(mode="visible_candidates"),
    )
    with pytest.raises(ValidationError, match="duplicate link rule id"):
        _resolved_session(
            nodes=(_satellite("leo-sat-p00s00"), _satellite("leo-sat-p00s01")),
            link_rules=(rule, rule),
        )


def test_link_candidate_maps_and_ground_candidates() -> None:
    sat = _satellite("leo-sat-p00s00")
    gs = _ground("ground-denver-router")
    rule = ResolvedLinkRule(
        rule_id="access",
        kind="access",
        enabled=True,
        endpoints=(
            ResolvedEndpoint(segment_id="ground", terminal_role="access", node_ids=(gs.node_id,)),
            ResolvedEndpoint(segment_id="leo", terminal_role="access", node_ids=(sat.node_id,)),
        ),
        topology=VisibleCandidatesTopology(mode="visible_candidates"),
    )
    candidate = ResolvedLinkCandidate(
        rule_id="access",
        kind="access",
        terminal_role="access",
        terminal_medium="rf",
        node_a=gs.node_id,
        node_b=sat.node_id,
        interface_a="term0",
        interface_b="gnd0",
        bandwidth_mbps=1000,
        topology_mode="visible_candidates",
        priority=0,
        endpoint_segments=("ground", "leo"),
    )
    rs = _resolved_session(
        nodes=(sat, gs),
        routing_domains=(_domain(sat.node_id, gs.node_id),),
        link_rules=(rule,),
        link_candidates=(candidate,),
    )

    assert rs.link_interface_map()[(gs.node_id, sat.node_id)] == ("term0", "gnd0")
    assert rs.link_bandwidth_map()[(gs.node_id, sat.node_id)] == 1000
    assert rs.ground_candidate_satellites_by_gs() == {gs.node_id: (sat.node_id,)}


def test_link_candidate_rejects_self_pair() -> None:
    with pytest.raises(ValidationError, match="identical endpoints"):
        ResolvedLinkCandidate(
            rule_id="r",
            kind="isl",
            terminal_role="isl",
            terminal_medium="optical",
            node_a="sat",
            node_b="sat",
            interface_a="isl0",
            interface_b="isl1",
            bandwidth_mbps=1,
            topology_mode="visible_candidates",
            priority=0,
            endpoint_segments=("leo", "leo"),
        )


def test_reversed_sid_block_rejected() -> None:
    with pytest.raises(ValidationError, match="reversed"):
        SidBlock(domain_id="earth_domain", node_ids=("sat",), sid_start=10, sid_end=5)


def test_sr_domain_requires_sid_block() -> None:
    with pytest.raises(ValidationError, match="missing sid_blocks"):
        _resolved_session(sid_blocks=())


def test_sid_block_for_non_sr_domain_rejected() -> None:
    domain = ResolvedRoutingDomain(
        domain_id="earth_domain",
        protocol="isis",
        node_ids=("leo-sat-p00s00",),
        capabilities=(),
    )
    with pytest.raises(ValidationError, match="without segment_routing"):
        _resolved_session(
            routing_domains=(domain,),
            sid_blocks=(_sid("leo-sat-p00s00"),),
        )


def test_sid_indices_are_allocated_from_resolved_domain_blocks() -> None:
    nodes = (_satellite("sat-a"), _satellite("sat-b"))
    rs = _resolved_session(
        nodes=nodes,
        routing_domains=(_domain("sat-a", "sat-b"),),
        sid_blocks=(_sid("sat-a", "sat-b", start=42),),
    )
    assert rs.sid_index_by_node_id() == {"sat-a": 42, "sat-b": 43}


def test_sid_block_size_must_match_domain_node_count() -> None:
    rs = _resolved_session(
        nodes=(_satellite("sat-a"), _satellite("sat-b")),
        routing_domains=(_domain("sat-a", "sat-b"),),
        sid_blocks=(
            SidBlock(
                domain_id="earth_domain", node_ids=("sat-a", "sat-b"), sid_start=42, sid_end=44
            ),
        ),
    )
    with pytest.raises(ValueError, match="has 3 index"):
        rs.sid_index_by_node_id()


def test_overlapping_sid_blocks_rejected() -> None:
    nodes = (_satellite("a", segment_id="seg-a"), _satellite("b", segment_id="seg-b"))
    domains = (
        _domain("a", domain_id="domain_a"),
        _domain("b", domain_id="domain_b"),
    )
    with pytest.raises(ValidationError, match="overlap"):
        _resolved_session(
            nodes=nodes,
            routing_domains=domains,
            sid_blocks=(
                SidBlock(domain_id="domain_a", node_ids=("a",), sid_start=100, sid_end=200),
                SidBlock(domain_id="domain_b", node_ids=("b",), sid_start=150, sid_end=300),
            ),
        )


def test_routing_domain_references_unknown_node_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown node"):
        _resolved_session(routing_domains=(_domain("ghost"),))


def test_resolved_runtime_identity_references_reject_empty_or_whitespace() -> None:
    bad_factories = [
        lambda: ResolvedTerminalBlock(
            terminal_id="",
            owner_node_id="sat",
            endpoint_role="isl",
            medium="optical",
            count=1,
            tracking_capacity=1,
            source_ref="x",
        ),
        lambda: ResolvedEndpoint(segment_id="leo", terminal_role="isl", node_ids=(" ",)),
        lambda: ResolvedLinkRule(
            rule_id="",
            kind="isl",
            enabled=True,
            endpoints=(
                ResolvedEndpoint(segment_id="leo", terminal_role="isl", node_ids=("sat-a",)),
                ResolvedEndpoint(segment_id="leo", terminal_role="isl", node_ids=("sat-b",)),
            ),
            topology=VisibleCandidatesTopology(mode="visible_candidates"),
        ),
        lambda: SidBlock(domain_id="", node_ids=("sat",), sid_start=100, sid_end=199),
        lambda: SourceContext(origin=""),
    ]
    for factory in bad_factories:
        with pytest.raises(ValidationError):
            factory()


def test_earth_multi_regime_supports_earth_constellation_and_ground() -> None:
    rs = RuntimeSupport.earth_multi_regime()
    assert rs.check_segment_kind("constellation") is None
    assert rs.check_segment_kind("ground_set") is None
    assert rs.check_central_body("earth") is None
    assert rs.check_reference_body("earth") is None


def test_earth_multi_regime_rejects_space_node_with_support_note() -> None:
    rs = RuntimeSupport.earth_multi_regime()
    feat = rs.check_segment_kind("space_node")
    assert isinstance(feat, UnsupportedFeature)
    assert feat.category is FeatureCategory.SEGMENT_KIND
    assert feat.support_note == "supported by the Earth-Luna runtime"


def test_earth_multi_regime_rejects_future_kinds_and_bodies() -> None:
    rs = RuntimeSupport.earth_multi_regime()
    assert rs.check_segment_kind("lagrange_point").support_note == "future runtime capability"
    assert rs.check_central_body("luna").support_note == "supported by the Earth-Luna runtime"
    assert rs.check_central_body("mars").support_note == "future runtime capability"
    assert rs.check_frame_body("sun").support_note == "future runtime capability"


def test_earth_multi_regime_rejects_protocol_adapters_and_ephemeris() -> None:
    rs = RuntimeSupport.earth_multi_regime()
    assert rs.check_protocol_adapter("static_ip") is not None
    assert rs.check_protocol_adapter("bgp") is not None
    assert rs.check_ephemeris_provider("skyfield_bsp") is not None


def test_unsupported_feature_error_message() -> None:
    rs = RuntimeSupport.earth_multi_regime()
    feats = [rs.check_segment_kind("space_node_set"), rs.check_central_body("mars")]
    err = UnsupportedFeatureError([f for f in feats if f])
    assert "space_node_set" in str(err)
    assert "mars" in str(err)
    assert len(err.features) == 2
