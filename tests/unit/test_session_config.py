"""Test session configuration models."""

import pytest
import yaml
from nodalarc.models.session import (
    AreaAssignmentConfig,
    SessionConfig,
)
from pydantic import ValidationError

from tests.conftest import FIXTURES_DIR

_SAMPLE_SESSION = {
    "session": {"name": "test-session"},
    "constellation": "configs/constellations/iridium-small-36.yaml",
    "ground_stations": "configs/ground-stations/sets/polar-emphasis.yaml",
    "routing": {
        "protocol": "isis",
        "extensions": ["sr"],
        "area_assignment": {"strategy": "flat", "gs_area_id": "49.0001"},
    },
    "time": {"step_seconds": 1},
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
        assert config.orbit.propagator == "keplerian-circular"
        assert config.dispatch.latency_authority == "ome"
        assert config.dispatch.substrate_compensation.rtt_to_one_way == "half-rtt"
        assert config.scheduling.ground.handover_mode == "bbm"
        assert config.observability.decision_trace.active_links == "always"
        assert config.observability.decision_trace.rejected_candidates_retention == "bounded"
        assert config.convergence.stability_period_s == 2.0
        assert config.convergence.timeout_s == 30.0
        assert config.convergence.probe_interval_ms == 100

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
            AreaAssignmentConfig(strategy="stripe")

    def test_stripe_rejects_zero(self):
        with pytest.raises(ValidationError, match="planes_per_stripe"):
            AreaAssignmentConfig(strategy="stripe", planes_per_stripe=0)

    def test_explicit_requires_assignments(self):
        with pytest.raises(ValidationError, match="assignments"):
            AreaAssignmentConfig(strategy="explicit")

    def test_flat_no_extra_fields_needed(self):
        config = AreaAssignmentConfig(strategy="flat")
        assert config.strategy == "flat"

    def test_per_plane_no_extra_fields_needed(self):
        config = AreaAssignmentConfig(strategy="per-plane")
        assert config.strategy == "per-plane"

    def test_explicit_with_assignments(self):
        config = AreaAssignmentConfig(
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
        data["simulation"] = {"schema_version": 1, "fidelity": "synthetic-keplerian"}
        with pytest.raises(ValidationError, match="schema_version must be 2"):
            SessionConfig.model_validate(data)

    def test_unknown_propagator_rejected(self):
        data = dict(_SAMPLE_SESSION)
        data["orbit"] = {"propagator": "unknown"}
        with pytest.raises(ValidationError, match="Input should be"):
            SessionConfig.model_validate(data)

    def test_sgp4_propagator_requires_matching_fidelity_and_tle_age_window(self):
        data = dict(_SAMPLE_SESSION)
        data["simulation"] = {"schema_version": 2, "fidelity": "sgp4-tle"}
        data["orbit"] = {"propagator": "sgp4-tle", "tle_max_age_days": 7.0}
        config = SessionConfig.model_validate(data)
        assert config.orbit.propagator == "sgp4-tle"
        assert config.orbit.tle_max_age_days == 7.0
        assert config.simulation.fidelity == "sgp4-tle"

    def test_sgp4_propagator_rejects_missing_tle_age_window(self):
        data = dict(_SAMPLE_SESSION)
        data["simulation"] = {"schema_version": 2, "fidelity": "sgp4-tle"}
        data["orbit"] = {"propagator": "sgp4-tle"}
        with pytest.raises(ValidationError, match="tle_max_age_days is required"):
            SessionConfig.model_validate(data)

    def test_tle_age_window_rejected_for_non_tle_propagators(self):
        data = dict(_SAMPLE_SESSION)
        data["orbit"] = {"propagator": "keplerian-circular", "tle_max_age_days": 7.0}
        with pytest.raises(ValidationError, match="only valid"):
            SessionConfig.model_validate(data)

    def test_j2_propagator_requires_matching_fidelity_label(self):
        data = dict(_SAMPLE_SESSION)
        data["simulation"] = {"schema_version": 2, "fidelity": "j2-mean-elements"}
        data["orbit"] = {"propagator": "j2-mean-elements"}
        config = SessionConfig.model_validate(data)
        assert config.orbit.propagator == "j2-mean-elements"
        assert config.simulation.fidelity == "j2-mean-elements"

    def test_fidelity_and_propagator_mismatch_rejected(self):
        data = dict(_SAMPLE_SESSION)
        data["simulation"] = {"schema_version": 2, "fidelity": "j2-mean-elements"}
        data["orbit"] = {"propagator": "keplerian-circular"}
        with pytest.raises(ValidationError, match="must describe the same physics model"):
            SessionConfig.model_validate(data)

    def test_mbb_requires_reserve_and_overlap(self):
        data = dict(_SAMPLE_SESSION)
        data["scheduling"] = {
            "ground": {
                "handover_mode": "mbb",
                "mbb_overlap_ticks": 0,
                "mbb_reserve": 0,
            }
        }
        with pytest.raises(ValidationError, match="MBB handover requires"):
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


class TestSessionFromFixture:
    def test_missing_stripe_config_rejected(self):
        data = yaml.safe_load((FIXTURES_DIR / "missing-stripe-config.yaml").read_text())
        with pytest.raises(ValidationError, match="planes_per_stripe"):
            SessionConfig.model_validate(data)
