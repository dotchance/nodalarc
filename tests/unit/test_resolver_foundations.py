# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Tests for the resolver foundations: ResolvedSession models + runtime support."""

import pytest
from nodalarc.models.identity import IdentityMode
from nodalarc.models.link_rules import VisibleCandidatesTopology
from nodalarc.models.resolved_session import (
    ResolvedEndpoint,
    ResolvedLinkRule,
    ResolvedNode,
    ResolvedSession,
    ResolvedTerminalBlock,
    SidBlock,
    SourceContext,
)
from nodalarc.models.session import (
    AddressingConfig,
    DispatchConfig,
    GroundSchedulingConfig,
    MiConfig,
    ObservabilityConfig,
    OrbitConfig,
    PlanePerNodePlacementConfig,
    RoutingConfig,
    SchedulingConfig,
    SessionMeta,
    SimulationConfig,
    TerrestrialLinkConfig,
    TimeConfig,
    TrafficFlowConfig,
)
from nodalarc.runtime_support import (
    FeatureCategory,
    RuntimeSupport,
    UnsupportedFeature,
    UnsupportedFeatureError,
)
from pydantic import ValidationError


def _terminal(node_id: str = "sat-p00s00") -> ResolvedTerminalBlock:
    return ResolvedTerminalBlock(
        terminal_id=f"{node_id}#isl[0]",
        owner_node_id=node_id,
        endpoint_role="isl",
        medium="optical",
        count=2,
        max_range_km=5000.0,
        bandwidth_mbps=10000.0,
        source_ref="satellite_type:demo#isl[0]",
    )


def _node(node_id: str = "sat-p00s00") -> ResolvedNode:
    return ResolvedNode(
        node_id=node_id,
        local_node_id="sat-P00S00",
        segment_id="default-space",
        namespace=None,
        kind="satellite",
        frame_id="earth",
        central_body="earth",
        satellite_type="demo",
        terminal_inventory=(_terminal(node_id),),
    )


def _resolved_session(**overrides) -> ResolvedSession:
    base = {
        "identity_mode": IdentityMode.SEGMENT_NAMESPACED,
        "session": SessionMeta(name="demo"),
        "nodes": (_node(),),
        "link_rules": (),
        "sid_blocks": (),
        "simulation": SimulationConfig(),
        "orbit": OrbitConfig(propagator="j2-mean-elements"),
        "routing": RoutingConfig(protocol="isis"),
        "dispatch": DispatchConfig(),
        "scheduling": SchedulingConfig(),
        "addressing": AddressingConfig(),
        "observability": ObservabilityConfig(),
        "time": TimeConfig(),
        "placement": PlanePerNodePlacementConfig(),
        "mi": MiConfig(),
        "source_context": SourceContext(origin="test"),
    }
    base.update(overrides)
    return ResolvedSession(**base)


# --- ResolvedSession / ResolvedNode models ---


def test_resolved_session_constructs_and_helpers():
    rs = _resolved_session()
    assert rs.identity_mode is IdentityMode.SEGMENT_NAMESPACED
    assert rs.node_ids() == ("sat-p00s00",)
    assert rs.node_by_id("sat-p00s00").local_node_id == "sat-P00S00"
    assert rs.node_by_id("missing") is None


def test_resolved_models_are_frozen():
    node = _node()
    with pytest.raises(ValidationError):
        node.node_id = "other"
    rs = _resolved_session()
    with pytest.raises(ValidationError):
        rs.identity_mode = IdentityMode.SEGMENT_NAMESPACED


def test_resolved_terminal_block_tracking_capacity_optional():
    block = _terminal()
    assert block.tracking_capacity is None  # satellite terminal: not applicable
    gnd = ResolvedTerminalBlock(
        terminal_id="gs-denver#ground[0]",
        owner_node_id="gs-denver",
        endpoint_role="ground",
        medium="rf",
        count=1,
        tracking_capacity=4,  # ground-station concept
        source_ref="station:denver#ground[0]",
    )
    assert gnd.tracking_capacity == 4


def test_resolved_node_exposes_both_identities():
    node = _node("leo-sat-p00s00")
    # node_id (runtime) and local_node_id (source) are distinct and both present.
    assert node.node_id == "leo-sat-p00s00"
    assert node.local_node_id == "sat-P00S00"


# --- Deep immutability across the boundary (nested mutation must fail) ---


def test_node_clock_is_frozen():
    node = _node()
    with pytest.raises(ValidationError):
        node.clock.model = "affine"


def test_embedded_config_is_frozen_nested():
    rs = _resolved_session()
    # Nested config carried on the resolved view is genuinely immutable.
    with pytest.raises(ValidationError):
        rs.simulation.actuation.expected_latency_ms = 999.0
    with pytest.raises(ValidationError):
        rs.time.compression = 5
    with pytest.raises(ValidationError):
        rs.orbit.propagator = "sgp4-tle"


# --- The resolved model cannot represent impossible truth ---


def test_duplicate_node_id_rejected():
    with pytest.raises(ValidationError, match="duplicate runtime node_id"):
        _resolved_session(nodes=(_node("dup"), _node("dup")))


def test_empty_endpoint_node_ids_rejected():
    with pytest.raises(ValidationError):
        ResolvedEndpoint(segment_id="s", terminal_role="ground", node_ids=())


def test_reversed_sid_block_rejected():
    with pytest.raises(ValidationError, match="reversed"):
        SidBlock(segment_id="leo", sid_start=10, sid_end=5)


def test_nonpositive_terminal_count_rejected():
    with pytest.raises(ValidationError):
        ResolvedTerminalBlock(
            terminal_id="t",
            owner_node_id="n",
            endpoint_role="isl",
            medium="optical",
            count=0,
            source_ref="x",
        )


def test_terminal_block_rejects_non_finite_range():
    import math

    with pytest.raises(ValidationError):
        ResolvedTerminalBlock(
            terminal_id="t",
            owner_node_id="n",
            endpoint_role="isl",
            medium="optical",
            count=1,
            max_range_km=math.inf,
            source_ref="x",
        )


def test_duplicate_terminal_id_within_node_rejected():
    blk = ResolvedTerminalBlock(
        terminal_id="dup",
        owner_node_id="n",
        endpoint_role="isl",
        medium="optical",
        count=1,
        source_ref="x",
    )
    with pytest.raises(ValidationError, match="duplicate terminal_id"):
        ResolvedNode(
            node_id="n",
            local_node_id="n",
            segment_id="s",
            namespace=None,
            kind="satellite",
            frame_id="earth",
            central_body="earth",
            terminal_inventory=(blk, blk),
        )


def test_terminal_owner_mismatch_rejected():
    blk = ResolvedTerminalBlock(
        terminal_id="t",
        owner_node_id="other-node",
        endpoint_role="isl",
        medium="optical",
        count=1,
        source_ref="x",
    )
    with pytest.raises(ValidationError, match="owner_node_id"):
        ResolvedNode(
            node_id="n",
            local_node_id="n",
            segment_id="s",
            namespace=None,
            kind="satellite",
            frame_id="earth",
            central_body="earth",
            terminal_inventory=(blk,),
        )


def test_ground_station_requires_ground_scheduling():
    with pytest.raises(ValidationError, match="ground_scheduling"):
        ResolvedNode(
            node_id="gs-denver",
            local_node_id="denver",
            segment_id="gnd",
            namespace=None,
            kind="ground_station",
            frame_id="earth-surface",
            reference_body="earth",
        )


def test_ground_station_terminal_requires_tracking_capacity():
    block = ResolvedTerminalBlock(
        terminal_id="gs-denver#ground[0]",
        owner_node_id="gs-denver",
        endpoint_role="ground",
        medium="rf",
        count=1,
        source_ref="station:denver#ground[0]",
    )
    with pytest.raises(ValidationError, match="tracking_capacity"):
        ResolvedNode(
            node_id="gs-denver",
            local_node_id="denver",
            segment_id="gnd",
            namespace=None,
            kind="ground_station",
            frame_id="earth-surface",
            reference_body="earth",
            terminal_inventory=(block,),
            ground_scheduling=GroundSchedulingConfig(),
        )


def test_ground_station_rejects_non_ground_terminal_role():
    block = ResolvedTerminalBlock(
        terminal_id="gs-denver#isl[0]",
        owner_node_id="gs-denver",
        endpoint_role="isl",
        medium="optical",
        count=1,
        tracking_capacity=1,
        source_ref="station:denver#isl[0]",
    )
    with pytest.raises(ValidationError, match="non-ground endpoint_role"):
        ResolvedNode(
            node_id="gs-denver",
            local_node_id="denver",
            segment_id="gnd",
            namespace=None,
            kind="ground_station",
            frame_id="earth-surface",
            reference_body="earth",
            terminal_inventory=(block,),
            ground_scheduling=GroundSchedulingConfig(),
        )


def test_non_ground_node_rejects_tracking_capacity():
    block = ResolvedTerminalBlock(
        terminal_id="sat#ground[0]",
        owner_node_id="sat",
        endpoint_role="ground",
        medium="rf",
        count=1,
        tracking_capacity=1,
        source_ref="satellite_type:demo#ground[0]",
    )
    with pytest.raises(ValidationError, match="must not set tracking_capacity"):
        ResolvedNode(
            node_id="sat",
            local_node_id="sat",
            segment_id="space",
            namespace=None,
            kind="satellite",
            frame_id="earth",
            central_body="earth",
            terminal_inventory=(block,),
        )


def test_ground_station_terminal_inventory_valid_when_complete():
    block = ResolvedTerminalBlock(
        terminal_id="gs-denver#ground[0]",
        owner_node_id="gs-denver",
        endpoint_role="ground",
        medium="rf",
        count=1,
        tracking_capacity=1,
        source_ref="station:denver#ground[0]",
    )
    node = ResolvedNode(
        node_id="gs-denver",
        local_node_id="denver",
        segment_id="gnd",
        namespace=None,
        kind="ground_station",
        frame_id="earth-surface",
        reference_body="earth",
        terminal_inventory=(block,),
        ground_scheduling=GroundSchedulingConfig(),
    )
    assert node.terminal_inventory[0].tracking_capacity == 1


def test_link_rule_endpoint_referencing_unknown_node_rejected():
    rule = ResolvedLinkRule(
        rule_id="r",
        kind="access",
        enabled=True,
        endpoints=(
            ResolvedEndpoint(segment_id="s", terminal_role="ground", node_ids=("ghost",)),
            ResolvedEndpoint(segment_id="space", terminal_role="ground", node_ids=("sat-p00s00",)),
        ),
        topology=VisibleCandidatesTopology(mode="visible_candidates"),
    )
    with pytest.raises(ValidationError, match="unknown"):
        _resolved_session(link_rules=(rule,))


# --- Runtime-support matrix ---


def test_m1_supports_earth_constellation_and_ground():
    rs = RuntimeSupport.mvp_m1()
    assert rs.check_segment_kind("constellation") is None
    assert rs.check_segment_kind("ground_set") is None
    assert rs.check_central_body("earth") is None
    assert rs.check_reference_body("earth") is None


def test_m1_rejects_space_node_with_milestone():
    rs = RuntimeSupport.mvp_m1()
    feat = rs.check_segment_kind("space_node")
    assert isinstance(feat, UnsupportedFeature)
    assert feat.category is FeatureCategory.SEGMENT_KIND
    assert feat.planned_milestone == "M3 (Luna)"


def test_m1_rejects_future_kinds_and_bodies():
    rs = RuntimeSupport.mvp_m1()
    assert rs.check_segment_kind("lagrange_point").planned_milestone == "post-MVP"
    assert rs.check_central_body("luna").planned_milestone == "M3 (Luna)"
    assert rs.check_central_body("mars").planned_milestone == "post-MVP"
    assert rs.check_frame_body("sun").planned_milestone == "post-MVP"


def test_m1_rejects_protocol_adapters_and_ephemeris():
    rs = RuntimeSupport.mvp_m1()
    # static_ip is structurally valid but M3; not yet runtime-supported in M1.
    assert rs.check_protocol_adapter("static_ip") is not None
    assert rs.check_protocol_adapter("bgp") is not None
    assert rs.check_ephemeris_provider("skyfield_bsp") is not None


def test_unsupported_feature_error_message():
    rs = RuntimeSupport.mvp_m1()
    feats = [rs.check_segment_kind("space_node_set"), rs.check_central_body("mars")]
    err = UnsupportedFeatureError([f for f in feats if f])
    assert "space_node_set" in str(err)
    assert "mars" in str(err)
    assert len(err.features) == 2


# --- Deep immutability: nested containers/models cannot be mutated after validation ---


def _resolved_rule(
    segment_id: str = "default-space",
    node_a: str = "sat-p00s00",
    node_b: str = "sat-p00s01",
):
    ep_a = ResolvedEndpoint(segment_id=segment_id, terminal_role="isl", node_ids=(node_a,))
    ep_b = ResolvedEndpoint(segment_id=segment_id, terminal_role="isl", node_ids=(node_b,))
    return ResolvedLinkRule(
        rule_id="r",
        kind="relay",
        enabled=True,
        endpoints=(ep_a, ep_b),
        topology=VisibleCandidatesTopology(mode="visible_candidates"),
    )


def test_routing_extensions_is_immutable():
    rs = _resolved_session(routing=RoutingConfig(protocol="isis", extensions=("te",)))
    assert rs.routing.extensions == ("te",)
    with pytest.raises(AttributeError):  # tuple, not list
        rs.routing.extensions.append("sr")


def test_routing_config_overrides_is_frozen():
    rs = _resolved_session(routing=RoutingConfig(protocol="isis", config_overrides={"x": 1}))
    assert rs.routing.config_overrides["x"] == 1  # readable
    with pytest.raises(TypeError):
        rs.routing.config_overrides["y"] = 2


def test_selection_policy_spec_is_frozen():
    rs = _resolved_session()
    with pytest.raises(ValidationError):
        rs.scheduling.ground.selection_policy.name = "lowest-elevation"
    with pytest.raises(TypeError):
        rs.scheduling.ground.handover_policy.params["discount_factor"] = 99.0


def test_link_rule_topology_is_frozen():
    rs = _resolved_session(
        nodes=(_node("sat-p00s00"), _node("sat-p00s01")), link_rules=(_resolved_rule(),)
    )
    with pytest.raises(ValidationError):
        rs.link_rules[0].topology.mode = "mutated"


def test_ranking_order_is_immutable_tuple():
    rs = _resolved_session()
    assert isinstance(rs.scheduling.ground.ranking_order, tuple)
    with pytest.raises(AttributeError):
        rs.scheduling.ground.ranking_order.append("service_priority")


# --- New invariants: endpoint segment membership + SID block segment membership ---


def test_endpoint_cross_segment_membership_rejected():
    # node sat-p00s00 belongs to "default-space"; an endpoint claiming "other" lies.
    rule = _resolved_rule(segment_id="other", node_a="sat-p00s00", node_b="sat-p00s01")
    with pytest.raises(ValidationError, match="another"):
        _resolved_session(nodes=(_node("sat-p00s00"), _node("sat-p00s01")), link_rules=(rule,))


def test_ghost_sid_block_rejected():
    with pytest.raises(ValidationError, match="no resolved nodes"):
        _resolved_session(sid_blocks=(SidBlock(segment_id="ghost", sid_start=1, sid_end=2),))


def test_sid_block_for_real_segment_ok():
    rs = _resolved_session(
        sid_blocks=(SidBlock(segment_id="default-space", sid_start=1, sid_end=10),)
    )
    assert rs.sid_blocks[0].segment_id == "default-space"


# --- Authoritative-boundary self-defense: overlap + duplicate intent ---


def test_overlapping_sid_blocks_rejected():
    def _seg_node(node_id: str, segment_id: str) -> ResolvedNode:
        return ResolvedNode(
            node_id=node_id,
            local_node_id=node_id,
            segment_id=segment_id,
            namespace=segment_id,
            kind="satellite",
            frame_id="earth",
            central_body="earth",
        )

    with pytest.raises(ValidationError, match="overlap"):
        _resolved_session(
            nodes=(_seg_node("a", "seg-a"), _seg_node("b", "seg-b")),
            sid_blocks=(
                SidBlock(segment_id="seg-a", sid_start=100, sid_end=200),
                SidBlock(segment_id="seg-b", sid_start=150, sid_end=300),
            ),
        )


def test_resolved_endpoint_rejects_duplicate_node_ids():
    with pytest.raises(ValidationError, match="duplicate node_id"):
        ResolvedEndpoint(
            segment_id="default-space",
            terminal_role="isl",
            node_ids=("sat-p00s00", "sat-p00s00"),
        )


def test_resolved_link_rule_rejects_endpoint_node_overlap():
    with pytest.raises(ValidationError, match="both endpoints"):
        ResolvedLinkRule(
            rule_id="r",
            kind="relay",
            enabled=True,
            endpoints=(
                ResolvedEndpoint(
                    segment_id="default-space",
                    terminal_role="isl",
                    node_ids=("sat-p00s00", "sat-p00s01"),
                ),
                ResolvedEndpoint(
                    segment_id="default-space",
                    terminal_role="isl",
                    node_ids=("sat-p00s01", "sat-p00s02"),
                ),
            ),
            topology=VisibleCandidatesTopology(mode="visible_candidates"),
        )


def test_duplicate_link_rule_id_rejected():
    with pytest.raises(ValidationError, match="duplicate link rule id"):
        _resolved_session(
            nodes=(_node("sat-p00s00"), _node("sat-p00s01")),
            link_rules=(_resolved_rule(), _resolved_rule()),
        )


def test_duplicate_traffic_flow_id_rejected():
    def flow(fid, src, dst):
        return TrafficFlowConfig(
            flow_id=fid,
            src=src,
            dst=dst,
            protocol="udp",
            bandwidth_kbps=1.0,
            probe_type="continuous",
        )

    with pytest.raises(ValidationError, match="traffic flow id"):
        _resolved_session(traffic_flows=(flow("f", "a", "b"), flow("f", "c", "d")))


def test_duplicate_terrestrial_link_pair_rejected():
    # Same pair in reverse order is the same physical link.
    with pytest.raises(ValidationError, match="terrestrial link"):
        _resolved_session(
            terrestrial_links=(
                TerrestrialLinkConfig(station_a="x", station_b="y"),
                TerrestrialLinkConfig(station_a="y", station_b="x"),
            )
        )


def test_resolved_runtime_identity_references_reject_empty_or_whitespace():
    bad_factories = [
        lambda: ResolvedTerminalBlock(
            terminal_id="",
            owner_node_id="sat-p00s00",
            endpoint_role="isl",
            medium="optical",
            count=1,
            source_ref="satellite_type:demo#isl[0]",
        ),
        lambda: ResolvedTerminalBlock(
            terminal_id="sat-p00s00#isl[0]",
            owner_node_id=" ",
            endpoint_role="isl",
            medium="optical",
            count=1,
            source_ref="satellite_type:demo#isl[0]",
        ),
        lambda: ResolvedNode(
            node_id="",
            local_node_id="sat-P00S00",
            segment_id="default-space",
            kind="satellite",
            frame_id="earth",
        ),
        lambda: ResolvedEndpoint(segment_id="", terminal_role="isl", node_ids=("sat-p00s00",)),
        lambda: ResolvedEndpoint(segment_id="default-space", terminal_role="isl", node_ids=(" ",)),
        lambda: ResolvedLinkRule(
            rule_id="",
            kind="relay",
            enabled=True,
            endpoints=(
                ResolvedEndpoint(
                    segment_id="default-space", terminal_role="isl", node_ids=("sat-p00s00",)
                ),
                ResolvedEndpoint(
                    segment_id="default-space", terminal_role="isl", node_ids=("sat-p00s01",)
                ),
            ),
            topology=VisibleCandidatesTopology(mode="visible_candidates"),
        ),
        lambda: SidBlock(segment_id="", sid_start=100, sid_end=199),
        lambda: SourceContext(origin=""),
    ]
    for factory in bad_factories:
        with pytest.raises(ValidationError):
            factory()
