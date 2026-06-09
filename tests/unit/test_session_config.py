"""Test session configuration models."""

import math

import pytest
import yaml
from nodalarc.models.session import (
    ActuationConfig,
    AreaAssignmentConfig,
    AreaMapping,
    ConvergenceConfig,
    ExplicitAreaAssignmentConfig,
    FlatAreaAssignmentConfig,
    OrbitConfig,
    PerPlaneAreaAssignmentConfig,
    PlacementConfig,
    PlaneGroupPerNodePlacementConfig,
    RoutingConfig,
    SessionConfig,
    StripeAreaAssignmentConfig,
    TerrestrialLinkConfig,
    TrafficFlowConfig,
)
from pydantic import TypeAdapter, ValidationError

from tests.conftest import FIXTURES_DIR


@pytest.mark.parametrize(
    "factory",
    [
        lambda v: ActuationConfig(expected_latency_ms=1.0, fault_after_ms=v),
        lambda v: OrbitConfig(propagator="sgp4-tle", tle_max_age_days=v),
        lambda v: TerrestrialLinkConfig(station_a="a", station_b="b", bandwidth_mbps=v),
        lambda v: ConvergenceConfig(timeout_s=v),
    ],
)
@pytest.mark.parametrize("bad", [math.inf, -math.inf, math.nan])
def test_physical_config_floats_reject_non_finite(factory, bad):
    with pytest.raises(ValidationError):
        factory(bad)


@pytest.mark.parametrize(
    "factory",
    [
        # Reachable runtime config: invalid finite values.
        lambda: ConvergenceConfig(probe_interval_ms=0),
        lambda: ConvergenceConfig(timeout_s=-1),
        lambda: TerrestrialLinkConfig(station_a="a", station_b="b", loss_pct=101),
        lambda: TerrestrialLinkConfig(station_a="a", station_b="b", latency_ms=-1),
        lambda: RoutingConfig(protocol="isis", compression_factor=-1),
        lambda: RoutingConfig(protocol="isis", bfd_rx_interval=0),
        lambda: RoutingConfig(protocol="ospf", ospf_dead_interval=-1),
        lambda: PlaneGroupPerNodePlacementConfig(policy="planeGroupPerNode", planes_per_group=0),
        lambda: TrafficFlowConfig(
            flow_id="f",
            src="a",
            dst="b",
            protocol="udp",
            bandwidth_kbps=-1,
            probe_type="continuous",
        ),
    ],
)
def test_reachable_config_rejects_impossible_finite_values(factory):
    with pytest.raises(ValidationError):
        factory()


def _source_catalog_bad_factories():
    from nodalarc.models.constellation import (
        GroundTerminal,
        IslTerminal,
        OrbitalElements,
        OrbitParams,
        PlaneParams,
    )
    from nodalarc.models.ground_station import GroundStationConfig
    from nodalarc.models.satellite_type import GroundTerminalDef as SatGroundTerminalDef
    from nodalarc.models.satellite_type import IslTerminalDef

    inf = math.inf
    return [
        lambda: IslTerminal(
            type="optical",
            count=1,
            max_range_km=inf,
            bandwidth_mbps=1e4,
            max_tracking_rate_deg_s=1.0,
        ),
        lambda: IslTerminal(
            type="optical",
            count=1,
            max_range_km=-5,
            bandwidth_mbps=1e4,
            max_tracking_rate_deg_s=1.0,
        ),
        lambda: GroundTerminal(type="rf", count=1, bandwidth_mbps=inf),
        lambda: OrbitParams(altitude_km=inf, inclination_deg=53.0, pattern="walker-delta"),
        lambda: OrbitalElements(
            altitude_km=550, inclination_deg=53, raan_deg=0, true_anomaly_deg=inf
        ),
        lambda: OrbitalElements(
            altitude_km=550, inclination_deg=200, raan_deg=0, true_anomaly_deg=0
        ),
        lambda: PlaneParams(count=-1, raan_spacing_deg=5, sats_per_plane=22, phase_offset_deg=0),
        lambda: PlaneParams(count=6, raan_spacing_deg=5, sats_per_plane=0, phase_offset_deg=0),
        lambda: IslTerminalDef(
            type="optical",
            count=1,
            max_range_km=inf,
            bandwidth_mbps=1e4,
            max_tracking_rate_deg_s=1.0,
        ),
        lambda: SatGroundTerminalDef(type="rf", count=1, bandwidth_mbps=inf),
        lambda: GroundStationConfig(name="x", lat_deg=0, lon_deg=0, alt_m=inf),
    ]


@pytest.mark.parametrize("idx", range(11))
def test_source_catalog_rejects_impossible_physics(idx):
    factory = _source_catalog_bad_factories()[idx]
    with pytest.raises(ValidationError):
        factory()


def _bad_selector_factories():
    from nodalarc.models.constellation import IslOverride, PlaneOverride, TLEFilter
    from nodalarc.models.ground_station import TerrestrialPrefixTemplate
    from nodalarc.models.link_rules import NodeSelector
    from nodalarc.models.session import AreaMapping

    return [
        lambda: PlaneOverride(planes=[-1], satellite_type="x"),
        lambda: PlaneOverride(planes=[], satellite_type="x"),
        lambda: PlaneOverride(planes=[1, 1], satellite_type="x"),
        lambda: AreaMapping(planes=[-1], area_id="49.0001"),
        lambda: AreaMapping(planes=[], area_id="49.0001"),
        lambda: AreaMapping(planes=[1, 1], area_id="49.0001"),
        lambda: AreaMapping(ground_stations=[], area_id="49.0001"),
        lambda: AreaMapping(ground_stations=["a", "a"], area_id="49.0001"),
        lambda: TerrestrialPrefixTemplate(default_route_metric=-1),
        lambda: TLEFilter(norad_ids=[]),
        lambda: TLEFilter(norad_ids=[-1]),
        lambda: TLEFilter(norad_ids=[1, 1]),
        lambda: IslOverride(node="sat-P00S00", links=[]),
        lambda: NodeSelector(segment="leo", planes=[]),
        lambda: NodeSelector(segment="leo", planes=[1, 1]),
        lambda: NodeSelector(segment="leo", names=["a", "a"]),
    ]


@pytest.mark.parametrize("idx", range(16))
def test_selectors_reject_empty_negative_duplicate(idx):
    factory = _bad_selector_factories()[idx]
    with pytest.raises(ValidationError):
        factory()


def test_valid_selectors_still_accepted():
    from nodalarc.models.constellation import PlaneOverride, TLEFilter
    from nodalarc.models.link_rules import NodeSelector
    from nodalarc.models.session import AreaMapping

    PlaneOverride(planes=[0, 6, 12], satellite_type="x")
    AreaMapping(ground_stations="all", area_id="0.0.0.0")  # "all" keyword
    TLEFilter(norad_ids=[25544, 43013])
    NodeSelector(all=(NodeSelector(segment="leo"), NodeSelector(plane=0)))


def test_routing_extensions_normalize_and_reject():
    # Known long-form aliases canonicalize so the stack resolver consumes them.
    assert RoutingConfig(protocol="isis", extensions=("traffic-engineering",)).extensions == ("te",)
    assert RoutingConfig(protocol="isis", extensions=("segment-routing",)).extensions == ("sr",)
    # Unknown / duplicate-after-normalization fail loud (no silent drop).
    for bad in [("not-real",), ("te", "te"), ("te", "traffic-engineering")]:
        with pytest.raises(ValidationError):
            RoutingConfig(protocol="isis", extensions=bad)


def test_routing_stack_is_rejected_as_split_brain_authority():
    for bad in [
        {"stack": "configs/routing-stacks/isis-te"},
        {"protocol": "isis", "stack": "configs/routing-stacks/isis-te"},
        {"protocol": "isis", "extensions": ("te",), "stack": "configs/routing-stacks/isis-te"},
    ]:
        with pytest.raises(ValidationError, match="routing.stack is not supported"):
            RoutingConfig(**bad)


def test_area_assignment_variants_reject_irrelevant_fields():
    adapter = TypeAdapter(AreaAssignmentConfig)
    bad_shapes = [
        {"strategy": "flat", "assignments": [{"planes": [0], "area_id": "49.0001"}]},
        {"strategy": "flat", "planes_per_stripe": 2},
        {"strategy": "per-plane", "assignments": [{"planes": [0], "area_id": "49.0001"}]},
        {"strategy": "per-plane", "planes_per_stripe": 2},
        {
            "strategy": "stripe",
            "planes_per_stripe": 2,
            "assignments": [{"planes": [0], "area_id": "49.0001"}],
        },
        {
            "strategy": "explicit",
            "planes_per_stripe": 2,
            "assignments": [{"planes": [0], "area_id": "49.0001"}],
        },
        {
            "strategy": "explicit",
            "assignments": [{"ground_stations": "denver", "area_id": "49.0001"}],
        },
    ]
    for shape in bad_shapes:
        with pytest.raises(ValidationError):
            adapter.validate_python(shape)


def test_placement_variants_reject_irrelevant_fields():
    adapter = TypeAdapter(PlacementConfig)
    bad_shapes = [
        {"policy": "allOnOne", "planes_per_group": 2},
        {"policy": "planePerNode", "planes_per_group": 2},
        {"policy": "planeGroupPerNode"},
        {"policy": "bogus"},
    ]
    for shape in bad_shapes:
        with pytest.raises(ValidationError):
            adapter.validate_python(shape)


def test_identity_reference_strings_reject_empty_or_whitespace():
    from nodalarc.models.constellation import IslLink, IslOverride

    bad_factories = [
        lambda: AreaMapping(planes=(0,), area_id=""),
        lambda: AreaMapping(ground_stations=(" ",), area_id="49.0001"),
        lambda: TerrestrialLinkConfig(station_a=" ", station_b="b"),
        lambda: TrafficFlowConfig(
            flow_id=" ",
            src="a",
            dst="b",
            protocol="udp",
            bandwidth_kbps=1,
            probe_type="continuous",
        ),
        lambda: IslLink(terminal="", peer="sat-P00S01"),
        lambda: IslLink(terminal="isl0", peer=" "),
        lambda: IslOverride(node="", links=[IslLink(terminal="isl0", peer="sat-P00S01")]),
    ]
    for factory in bad_factories:
        with pytest.raises(ValidationError):
            factory()


def test_legacy_catalog_reference_strings_reject_empty_or_whitespace():
    from nodalarc.models.ground_station import GroundStationConfig, GroundStationSetConfig
    from nodalarc.models.routing_stack import ConfigTemplate, RoutingStackConfig
    from nodalarc.models.satellite_type import GroundTerminalDef as SatGroundTerminalDef
    from nodalarc.models.satellite_type import IslTerminalDef, SatelliteTypeConfig

    bad_factories = [
        lambda: SessionConfig.model_validate({**_SAMPLE_SESSION, "session": {"name": ""}}),
        lambda: SessionConfig.model_validate({**_SAMPLE_SESSION, "constellation": ""}),
        lambda: SessionConfig.model_validate({**_SAMPLE_SESSION, "ground_stations": " "}),
        lambda: GroundStationConfig(name="", lat_deg=0, lon_deg=0),
        lambda: GroundStationSetConfig(name="demo", stations=[""]),
        lambda: IslTerminalDef(
            type="",
            count=1,
            max_range_km=1000,
            bandwidth_mbps=1000,
            max_tracking_rate_deg_s=1,
        ),
        lambda: SatGroundTerminalDef(type=" ", count=1, bandwidth_mbps=1000),
        lambda: SatelliteTypeConfig(name="", isl_terminals=[], ground_terminals=[]),
        lambda: ConfigTemplate(src="", dst="/etc/frr/frr.conf"),
        lambda: ConfigTemplate(src="frr.conf.j2", dst=" "),
        lambda: RoutingStackConfig(
            name="",
            image="frrouting/frr",
            config_templates=[ConfigTemplate(src="frr.conf.j2", dst="/etc/frr/frr.conf")],
        ),
    ]
    for factory in bad_factories:
        with pytest.raises(ValidationError):
            factory()


@pytest.mark.parametrize(
    "pairs",
    [
        [{"a": "n1", "b": "n1"}],  # self-pair
        [{"a": "n1", "b": "n2"}, {"a": "n1", "b": "n2"}],  # exact duplicate
        [{"a": "n1", "b": "n2"}, {"a": "n2", "b": "n1"}],  # reversed duplicate (undirected)
    ],
)
def test_explicit_pairs_topology_rejects_bad_pairs(pairs):
    from nodalarc.models.link_rules import ExplicitPairsTopology

    with pytest.raises(ValidationError):
        ExplicitPairsTopology(mode="explicit_pairs", pairs=pairs)


def test_isl_override_rejects_duplicate_terminal():
    from nodalarc.models.constellation import IslLink, IslOverride

    with pytest.raises(ValidationError, match="same terminal"):
        IslOverride(
            node="sat-P00S00",
            links=[
                IslLink(terminal="isl0", peer="sat-P01S00"),
                IslLink(terminal="isl0", peer="sat-P02S00"),
            ],
        )


def test_area_assignment_fails_loud_on_bad_config():
    from nodalarc.models.session import AreaMapping

    with pytest.raises(ValidationError):  # unknown strategy
        TypeAdapter(AreaAssignmentConfig).validate_python({"strategy": "typo"})
    with pytest.raises(ValidationError):  # mapping targets nothing
        AreaMapping(area_id="49.1")
    with pytest.raises(ValidationError):  # duplicate plane mapping (last-write-win)
        ExplicitAreaAssignmentConfig(
            strategy="explicit",
            assignments=[
                AreaMapping(planes=(1,), area_id="a"),
                AreaMapping(planes=(1,), area_id="b"),
            ],
        )


def test_resolve_stack_is_the_extension_owning_boundary():
    from nodalarc.stack_resolver import resolve_stack

    with pytest.raises(ValueError):  # raw API rejects unknown, not silently ignores
        resolve_stack("isis", ["not-real"])
    # traffic-engineering normalizes to te and is actually consumed (not dropped).
    assert resolve_stack("isis", ["traffic-engineering"]) != resolve_stack("isis", [])


@pytest.mark.parametrize(
    "factory",
    [
        lambda: TerrestrialLinkConfig(station_a="x", station_b="x"),  # self-link
        lambda: TerrestrialLinkConfig(station_a="", station_b="y"),  # empty id
        lambda: TrafficFlowConfig(
            flow_id="f", src="a", dst="a", protocol="udp", bandwidth_kbps=1, probe_type="continuous"
        ),  # src == dst
        lambda: TrafficFlowConfig(
            flow_id="", src="a", dst="b", protocol="udp", bandwidth_kbps=1, probe_type="continuous"
        ),  # empty flow_id
    ],
)
def test_terrestrial_and_traffic_objects_reject_impossible_intent(factory):
    with pytest.raises(ValidationError):
        factory()


def _ground_scheduling(**overrides):
    ground = {
        "selection_policy": {"name": "highest-elevation", "params": {}},
        "handover_policy": {
            "name": "hysteresis",
            "params": {"discount_factor": 1.15, "mask_fade_range_deg": 5.0},
        },
        "handover_mode": "bbm",
        "mbb_overlap_ticks": 3,
        "mbb_reserve": 0,
    }
    ground.update(overrides)
    return ground


_SAMPLE_SESSION = {
    "session": {"name": "test-session"},
    "constellation": "configs/constellations/iridium-small-36.yaml",
    "ground_stations": "configs/ground-stations/sets/polar-emphasis.yaml",
    "orbit": {"propagator": "keplerian-circular"},
    "routing": {
        "protocol": "isis",
        "extensions": ["sr"],
        "area_assignment": {"strategy": "flat", "gs_area_id": "49.0001"},
    },
    "time": {"step_seconds": 1},
    "scheduling": {"ground": _ground_scheduling()},
    "traffic_flows": [
        {
            "flow_id": "test",
            "src": "gs-svalbard",
            "dst": "gs-mcmurdo",
            "protocol": "udp",
            "bandwidth_kbps": 100,
            "probe_type": "continuous",
        },
    ],
    "convergence": {"stability_period_s": 2.0, "timeout_s": 30.0},
}


class TestSessionConfigLoading:
    def test_session_loads(self):
        config = SessionConfig.model_validate(_SAMPLE_SESSION)
        assert config.session.name == "test-session"
        assert config.constellation == "configs/constellations/iridium-small-36.yaml"
        assert config.routing.area_assignment.strategy == "flat"

    def test_defaults_applied(self):
        config = SessionConfig.model_validate(_SAMPLE_SESSION)
        assert config.addressing.sat_id_template == "sat-P{plane:02d}S{slot:02d}"
        assert config.addressing.gs_id_template == "gs-{name}"
        assert config.time.step_seconds == 1
        assert config.simulation.schema_version == 2
        assert config.simulation.ground_link_model == "terminal_physics"
        assert config.simulation.acknowledge_geometry_only is False
        assert config.simulation.acknowledge_bbm_handover_gap is False
        assert config.orbit.propagator == "keplerian-circular"
        assert config.dispatch.latency_authority == "ome"
        assert config.dispatch.clean_kernel_audit_interval_s == 60.0
        assert config.dispatch.substrate_compensation.rtt_to_one_way == "half-rtt"
        assert config.scheduling.ground.handover_mode == "bbm"
        assert config.observability.decision_trace.active_links == "always"
        assert config.observability.decision_trace.rejected_candidates_retention == "bounded"
        assert config.convergence.stability_period_s == 2.0
        assert config.convergence.timeout_s == 30.0
        assert config.convergence.probe_interval_ms == 100

    def test_bbm_handover_gap_acknowledgement_is_explicit_simulation_field(self):
        data = dict(_SAMPLE_SESSION)
        data["simulation"] = {"acknowledge_bbm_handover_gap": True}
        config = SessionConfig.model_validate(data)
        assert config.simulation.acknowledge_bbm_handover_gap is True

    def test_actuation_contract_defaults(self):
        # The wall-clock in_flight -> faulted bound is a session contract, not a
        # frontend constant. Defaults reflect measured single-pair actuation (~37ms p99).
        config = SessionConfig.model_validate(_SAMPLE_SESSION)
        assert config.simulation.actuation.expected_latency_ms == 250.0
        assert config.simulation.actuation.fault_after_ms == 1200.0

    def test_actuation_contract_overridable_per_session(self):
        data = dict(_SAMPLE_SESSION)
        data["simulation"] = {"actuation": {"expected_latency_ms": 200.0, "fault_after_ms": 1000.0}}
        config = SessionConfig.model_validate(data)
        assert config.simulation.actuation.expected_latency_ms == 200.0
        assert config.simulation.actuation.fault_after_ms == 1000.0

    def test_actuation_fault_threshold_must_exceed_expected(self):
        data = dict(_SAMPLE_SESSION)
        data["simulation"] = {
            "actuation": {"expected_latency_ms": 1000.0, "fault_after_ms": 1000.0}
        }
        with pytest.raises(ValidationError, match="fault_after_ms must exceed expected_latency_ms"):
            SessionConfig.model_validate(data)

    def test_actuation_bounds_must_be_positive(self):
        data = dict(_SAMPLE_SESSION)
        data["simulation"] = {"actuation": {"fault_after_ms": -5.0}}
        with pytest.raises(ValidationError, match="must be > 0 ms"):
            SessionConfig.model_validate(data)

    def test_actuation_rejects_unknown_field(self):
        data = dict(_SAMPLE_SESSION)
        data["simulation"] = {"actuation": {"grace_ms": 500.0}}
        with pytest.raises(ValidationError):
            SessionConfig.model_validate(data)

    def test_ground_policy_surface_must_be_explicit(self):
        data = dict(_SAMPLE_SESSION)
        data.pop("scheduling")
        with pytest.raises(ValidationError, match="Ground scheduling policy must be explicit"):
            SessionConfig.model_validate(data)

    def test_handover_policy_must_be_explicit(self):
        data = dict(_SAMPLE_SESSION)
        ground = _ground_scheduling()
        ground.pop("handover_policy")
        data["scheduling"] = {"ground": ground}
        with pytest.raises(ValidationError, match="scheduling.ground.handover_policy"):
            SessionConfig.model_validate(data)

    def test_round_trip(self):
        config = SessionConfig.model_validate(_SAMPLE_SESSION)
        json_str = config.model_dump_json()
        restored = SessionConfig.model_validate_json(json_str)
        assert restored == config

    def test_traffic_flows_present(self):
        config = SessionConfig.model_validate(_SAMPLE_SESSION)
        assert config.traffic_flows is not None
        assert len(config.traffic_flows) == 1


class TestAreaAssignmentValidation:
    def test_stripe_requires_planes_per_stripe(self):
        with pytest.raises(ValidationError, match="planes_per_stripe"):
            StripeAreaAssignmentConfig(strategy="stripe")

    def test_stripe_rejects_zero(self):
        with pytest.raises(ValidationError, match="planes_per_stripe"):
            StripeAreaAssignmentConfig(strategy="stripe", planes_per_stripe=0)

    def test_explicit_requires_assignments(self):
        with pytest.raises(ValidationError, match="assignments"):
            ExplicitAreaAssignmentConfig(strategy="explicit")

    def test_flat_no_extra_fields_needed(self):
        config = FlatAreaAssignmentConfig(strategy="flat")
        assert config.strategy == "flat"

    def test_per_plane_no_extra_fields_needed(self):
        config = PerPlaneAreaAssignmentConfig(strategy="per-plane")
        assert config.strategy == "per-plane"

    def test_explicit_with_assignments(self):
        config = ExplicitAreaAssignmentConfig(
            strategy="explicit",
            assignments=[
                {"planes": [0, 1], "area_id": "49.0001"},
                {"planes": [2, 3], "area_id": "49.0002"},
            ],
        )
        assert len(config.assignments) == 2
        assert config.assignments[0].area_id == "49.0001"


class TestEngineConfigValidation:
    def test_bad_schema_version_rejected(self):
        data = dict(_SAMPLE_SESSION)
        data["simulation"] = {"schema_version": 1}
        with pytest.raises(ValidationError, match="schema_version must be 2"):
            SessionConfig.model_validate(data)

    def test_ground_link_model_is_ground_physics_mode_not_propagator_knob(self):
        data = dict(_SAMPLE_SESSION)
        data["simulation"] = {"schema_version": 2, "ground_link_model": "j2-mean-elements"}
        with pytest.raises(ValidationError, match="Input should be"):
            SessionConfig.model_validate(data)

    def test_geometry_only_ground_link_model_requires_validator_acknowledgement_not_model_default(
        self,
    ):
        data = dict(_SAMPLE_SESSION)
        data["simulation"] = {"schema_version": 2, "ground_link_model": "geometry_only"}
        config = SessionConfig.model_validate(data)
        assert config.simulation.ground_link_model == "geometry_only"
        assert config.simulation.acknowledge_geometry_only is False
        assert config.simulation.acknowledge_bbm_handover_gap is False

    def test_unknown_propagator_rejected(self):
        data = dict(_SAMPLE_SESSION)
        data["orbit"] = {"propagator": "unknown"}
        with pytest.raises(ValidationError, match="Input should be"):
            SessionConfig.model_validate(data)

    def test_orbit_propagator_is_required(self):
        data = dict(_SAMPLE_SESSION)
        data.pop("orbit")
        with pytest.raises(ValidationError, match="orbit"):
            SessionConfig.model_validate(data)

    def test_sgp4_propagator_requires_tle_age_window(self):
        data = dict(_SAMPLE_SESSION)
        data["orbit"] = {"propagator": "sgp4-tle", "tle_max_age_days": 7.0}
        config = SessionConfig.model_validate(data)
        assert config.orbit.propagator == "sgp4-tle"
        assert config.orbit.tle_max_age_days == 7.0
        assert config.orbit.fidelity_label == "sgp4-tle"

    def test_sgp4_propagator_rejects_missing_tle_age_window(self):
        data = dict(_SAMPLE_SESSION)
        data["orbit"] = {"propagator": "sgp4-tle"}
        with pytest.raises(ValidationError, match="tle_max_age_days is required"):
            SessionConfig.model_validate(data)

    def test_tle_age_window_rejected_for_non_tle_propagators(self):
        data = dict(_SAMPLE_SESSION)
        data["orbit"] = {"propagator": "keplerian-circular", "tle_max_age_days": 7.0}
        with pytest.raises(ValidationError, match="only valid"):
            SessionConfig.model_validate(data)

    def test_j2_propagator_derives_fidelity_label(self):
        data = dict(_SAMPLE_SESSION)
        data["orbit"] = {"propagator": "j2-mean-elements"}
        config = SessionConfig.model_validate(data)
        assert config.orbit.propagator == "j2-mean-elements"
        assert config.orbit.fidelity_label == "j2-mean-elements"

    def test_mbb_requires_reserve_and_overlap(self):
        data = dict(_SAMPLE_SESSION)
        data["scheduling"] = {
            "ground": _ground_scheduling(
                handover_mode="mbb",
                mbb_overlap_ticks=0,
                mbb_reserve=0,
            )
        }
        with pytest.raises(ValidationError, match="MBB handover requires"):
            SessionConfig.model_validate(data)

    def test_mbb_reserve_above_one_requires_future_multi_overlap_support(self):
        data = dict(_SAMPLE_SESSION)
        data["scheduling"] = {
            "ground": _ground_scheduling(
                handover_mode="mbb",
                mbb_overlap_ticks=3,
                mbb_reserve=2,
            )
        }
        with pytest.raises(ValidationError, match="multi-overlap allocator support"):
            SessionConfig.model_validate(data)

    def test_routing_rejects_deprecated_mbb_fields(self):
        data = dict(_SAMPLE_SESSION)
        data["routing"] = {
            **_SAMPLE_SESSION["routing"],
            "mbb_dispatch": True,
        }
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            SessionConfig.model_validate(data)

    def test_substrate_rtt_policy_is_explicit_half_rtt(self):
        data = dict(_SAMPLE_SESSION)
        data["dispatch"] = {
            "substrate_compensation": {
                "measurement_source": "node-agent-rtt",
                "rtt_to_one_way": "half",
            }
        }
        with pytest.raises(ValidationError, match="half-rtt"):
            SessionConfig.model_validate(data)

    def test_clean_kernel_audit_interval_must_be_positive(self):
        data = dict(_SAMPLE_SESSION)
        data["dispatch"] = {"clean_kernel_audit_interval_s": 0}
        with pytest.raises(ValidationError, match="clean_kernel_audit_interval_s must be > 0"):
            SessionConfig.model_validate(data)

    def test_time_values_must_be_positive(self):
        data = dict(_SAMPLE_SESSION)
        data["time"] = {"step_seconds": 0}
        with pytest.raises(ValidationError, match="must be >= 1"):
            SessionConfig.model_validate(data)

    def test_active_decision_trace_cannot_be_disabled(self):
        data = dict(_SAMPLE_SESSION)
        data["observability"] = {
            "decision_trace": {
                "active_links": "none",
            }
        }
        with pytest.raises(ValidationError, match="always"):
            SessionConfig.model_validate(data)

    def test_ranking_order_must_end_with_lex_pair(self):
        data = dict(_SAMPLE_SESSION)
        data["scheduling"] = {
            "ground": _ground_scheduling(
                ranking_order=["selection_score", "service_priority"],
            )
        }
        with pytest.raises(ValidationError, match="lex_pair"):
            SessionConfig.model_validate(data)

    def test_ranking_order_accepts_operator_component_order(self):
        data = dict(_SAMPLE_SESSION)
        data["scheduling"] = {
            "ground": _ground_scheduling(
                ranking_order=["selection_score", "service_priority", "lex_pair"],
            )
        }
        config = SessionConfig.model_validate(data)
        assert config.scheduling.ground.ranking_order == (
            "selection_score",
            "service_priority",
            "lex_pair",
        )

    def test_reserved_cross_tenant_displacement_policy_is_rejected(self):
        data = dict(_SAMPLE_SESSION)
        data["scheduling"] = {
            "ground": _ground_scheduling(
                cross_tenant_displacement="by_service_priority",
            )
        }
        with pytest.raises(ValidationError, match="cross_tenant_displacement"):
            SessionConfig.model_validate(data)

    def test_reserved_mbb_preemption_policy_is_rejected(self):
        data = dict(_SAMPLE_SESSION)
        data["scheduling"] = {
            "ground": _ground_scheduling(
                mbb_preemption="by_priority",
            )
        }
        with pytest.raises(ValidationError, match="mbb_preemption"):
            SessionConfig.model_validate(data)

    def test_multi_tick_bbm_acquire_timeout_is_rejected_until_wait_state_exists(self):
        data = dict(_SAMPLE_SESSION)
        data["scheduling"] = {
            "ground": _ground_scheduling(
                bbm_acquire_timeout_ticks=2,
            )
        }
        with pytest.raises(ValidationError, match="bbm_acquire_timeout_ticks"):
            SessionConfig.model_validate(data)

    def test_longest_remaining_pass_requires_lookahead_horizon(self):
        data = dict(_SAMPLE_SESSION)
        data["scheduling"] = {
            "ground": _ground_scheduling(
                selection_policy={"name": "longest-remaining-pass", "params": {}},
            )
        }
        with pytest.raises(ValidationError, match="lookahead_horizon_ticks"):
            SessionConfig.model_validate(data)

    def test_longest_remaining_pass_accepts_explicit_lookahead_horizon(self):
        data = dict(_SAMPLE_SESSION)
        data["scheduling"] = {
            "ground": _ground_scheduling(
                selection_policy={
                    "name": "longest-remaining-pass",
                    "params": {"lookahead_horizon_ticks": 600},
                },
            )
        }
        config = SessionConfig.model_validate(data)
        assert config.scheduling.ground.selection_policy.name == "longest-remaining-pass"
        assert config.scheduling.ground.selection_policy.params["lookahead_horizon_ticks"] == 600


class TestSessionFromFixture:
    def test_missing_stripe_config_rejected(self):
        data = yaml.safe_load((FIXTURES_DIR / "missing-stripe-config.yaml").read_text())
        with pytest.raises(ValidationError, match="planes_per_stripe"):
            SessionConfig.model_validate(data)
