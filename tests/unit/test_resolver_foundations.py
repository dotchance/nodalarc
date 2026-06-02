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
    MiConfig,
    ObservabilityConfig,
    OrbitConfig,
    PlacementConfig,
    RoutingConfig,
    SchedulingConfig,
    SessionMeta,
    SimulationConfig,
    TimeConfig,
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
        "identity_mode": IdentityMode.LEGACY_IDENTITY,
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
        "placement": PlacementConfig(),
        "mi": MiConfig(),
        "source_context": SourceContext(origin="test"),
    }
    base.update(overrides)
    return ResolvedSession(**base)


# --- ResolvedSession / ResolvedNode models ---


def test_resolved_session_constructs_and_helpers():
    rs = _resolved_session()
    assert rs.identity_mode is IdentityMode.LEGACY_IDENTITY
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
            terminal_inventory=(blk,),
        )


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
