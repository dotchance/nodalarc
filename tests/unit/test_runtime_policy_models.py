"""Tests for runtime policy helpers that are not persisted session roots."""

from datetime import UTC, datetime

import pytest
from nodalarc.models.segment_session import TimeConfig
from nodalarc.models.session import (
    GroundSchedulingConfig,
    TrafficFlowConfig,
    resolve_session_epoch,
)
from pydantic import ValidationError


def test_ground_scheduling_defaults_are_runtime_safe() -> None:
    config = GroundSchedulingConfig()

    assert config.selection_policy.name == "highest-elevation"
    assert config.handover_policy.name == "hysteresis"
    assert config.ranking_order[-1] == "lex_pair"
    assert config.handover_mode == "bbm"
    assert config.mbb_reserve == 0


def test_ground_scheduling_normalizes_hysteresis_parameters() -> None:
    config = GroundSchedulingConfig(
        handover_policy={
            "name": "hysteresis",
            "params": {"discount_factor": 1.25},
        }
    )

    assert config.handover_policy.params["discount_factor"] == 1.25
    assert config.handover_policy.params["mask_fade_range_deg"] == 5.0


@pytest.mark.parametrize(
    "settings, message",
    [
        (
            {"handover_mode": "mbb", "mbb_overlap_ticks": 0, "mbb_reserve": 1},
            "mbb_overlap_ticks",
        ),
        (
            {"handover_mode": "mbb", "mbb_overlap_ticks": 3, "mbb_reserve": 0},
            "mbb_reserve",
        ),
        ({"mbb_reserve": 2}, "multi-overlap allocator support"),
        ({"bbm_acquire_timeout_ticks": 2}, "bbm_acquire_timeout_ticks"),
    ],
)
def test_ground_scheduling_rejects_unimplemented_behavior(settings, message) -> None:
    with pytest.raises(ValidationError, match=message):
        GroundSchedulingConfig(**settings)


@pytest.mark.parametrize(
    "ranking_order",
    [
        (),
        ("lex_pair",),
        ("selection_score", "service_priority"),
        ("service_priority", "service_priority", "lex_pair"),
    ],
)
def test_ground_scheduling_rejects_ambiguous_ranking(ranking_order) -> None:
    with pytest.raises(ValidationError, match="ranking_order"):
        GroundSchedulingConfig(ranking_order=ranking_order)


def test_ground_scheduling_accepts_longest_remaining_pass_with_horizon() -> None:
    config = GroundSchedulingConfig(
        selection_policy={
            "name": "longest-remaining-pass",
            "params": {"lookahead_horizon_ticks": 600},
        }
    )

    assert config.selection_policy.params["lookahead_horizon_ticks"] == 600


@pytest.mark.parametrize(
    "values",
    [
        {
            "flow_id": "flow-a",
            "src": "node-a",
            "dst": "node-a",
            "protocol": "udp",
            "bandwidth_kbps": 10,
            "probe_type": "continuous",
        },
        {
            "flow_id": "flow-a",
            "src": "node-a",
            "dst": "node-b",
            "protocol": "udp",
            "bandwidth_kbps": 0,
            "probe_type": "continuous",
        },
        {
            "flow_id": " ",
            "src": "node-a",
            "dst": "node-b",
            "protocol": "udp",
            "bandwidth_kbps": 10,
            "probe_type": "continuous",
        },
    ],
)
def test_scenario_traffic_flow_rejects_invalid_intent(values) -> None:
    with pytest.raises(ValidationError):
        TrafficFlowConfig.model_validate(values)


def test_resolve_session_epoch_uses_canonical_time_config() -> None:
    config = TimeConfig(
        start_time="2020-01-01T00:00:00+00:00",
        step_seconds=1,
        compression=1,
    )

    assert resolve_session_epoch(config) == datetime(2020, 1, 1, tzinfo=UTC).timestamp()
