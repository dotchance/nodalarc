"""Tests for NodalPathPlatformConfig Pydantic model and singleton."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nodalpath.platform import (
    NodalPathPlatformConfig,
    get_nodalpath_config,
    init_nodalpath_config,
    reset_nodalpath_config,
)


def _valid_config_dict() -> dict:
    return {
        "platform_config_path": "configs/platform.yaml",
        "satellite_sid_range_start": 16000,
        "ground_station_sid_range_start": 24000,
        "grpc_push_timeout_seconds": 10,
        "grpc_push_max_parallel_workers": 20,
        "lookahead_horizon_sim_seconds": 5700,
        "lookahead_poll_interval_seconds": 5.0,
        "push_lead_time_sim_seconds": 3,
        "inspection_max_retained_runs": 50,
        "inspection_heartbeat_interval_seconds": 0,
        "console_push_history_max_entries": 100,
        "console_deviation_history_max_entries": 100,
        "console_almanac_history_max_entries": 200,
        "console_event_log_max_entries": 300,
    }


class TestNodalPathPlatformConfig:
    def test_validates_from_dict(self):
        cfg = NodalPathPlatformConfig(**_valid_config_dict())
        assert cfg.satellite_sid_range_start == 16000

    def test_frozen(self):
        cfg = NodalPathPlatformConfig(**_valid_config_dict())
        with pytest.raises(ValidationError):
            cfg.satellite_sid_range_start = 99999

    def test_missing_field_raises(self):
        d = _valid_config_dict()
        del d["satellite_sid_range_start"]
        with pytest.raises(ValidationError):
            NodalPathPlatformConfig(**d)


class TestSingleton:
    def setup_method(self):
        reset_nodalpath_config()

    def teardown_method(self):
        # Re-initialize with standard values so other tests still work
        init_nodalpath_config(NodalPathPlatformConfig(**_valid_config_dict()))

    def test_get_before_init_raises(self):
        with pytest.raises(RuntimeError, match="not initialized"):
            get_nodalpath_config()

    def test_init_from_object(self):
        cfg = NodalPathPlatformConfig(**_valid_config_dict())
        result = init_nodalpath_config(cfg)
        assert result is cfg
        assert get_nodalpath_config() is cfg
