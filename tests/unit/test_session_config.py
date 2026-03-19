"""Test session configuration models."""

import pytest
import yaml
from pydantic import ValidationError

from nodalarc.models.session import (
    AreaAssignmentConfig,
    SessionConfig,
)
from tests.conftest import CONFIGS_DIR, FIXTURES_DIR


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
        {"flow_id": "test", "src": "gs-svalbard", "dst": "gs-mcmurdo",
         "protocol": "udp", "bandwidth_kbps": 100, "probe_type": "continuous"},
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


class TestSessionFromFixture:
    def test_missing_stripe_config_rejected(self):
        data = yaml.safe_load((FIXTURES_DIR / "missing-stripe-config.yaml").read_text())
        with pytest.raises(ValidationError, match="planes_per_stripe"):
            SessionConfig.model_validate(data)
