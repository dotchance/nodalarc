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
        assert config.scheduling.ground.handover_mode == "bbm"
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
        data["orbit"] = {"propagator": "sgp4-tle"}
        with pytest.raises(ValidationError, match="Input should be 'keplerian-circular'"):
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


class TestSessionFromFixture:
    def test_missing_stripe_config_rejected(self):
        data = yaml.safe_load((FIXTURES_DIR / "missing-stripe-config.yaml").read_text())
        with pytest.raises(ValidationError, match="planes_per_stripe"):
            SessionConfig.model_validate(data)
