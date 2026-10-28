# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""OME epoch-commit ordering contracts."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import ome.main as ome_main
from nodalarc.models.events import (
    PlaybackControlAccepted,
    PlaybackControlCommand,
    PlaybackControlRefused,
    PlaybackState,
)
from nodalarc.models.link_decisions import GroundPolicyAudit
from ome.types import GroundVisibilityDecision


def _policy_audit() -> GroundPolicyAudit:
    return GroundPolicyAudit(
        selection_policies={"gs-fixed": "highest-elevation"},
        selection_policy_params={"gs-fixed": {}},
        handover_policies={"gs-fixed": "hysteresis"},
        handover_policy_params={"gs-fixed": {"discount_factor": 1.15, "mask_fade_range_deg": 5.0}},
        ranking_order=("service_priority", "selection_score", "lex_pair"),
        handover_mode="bbm",
        handover_modes={"gs-fixed": "bbm"},
        mbb_preemption="off",
        successor_abort_policy="hard_release",
        cross_tenant_displacement="off",
        mbb_overlap_ticks=3,
        mbb_overlap_ticks_by_gs={"gs-fixed": 3},
        mbb_reserve=0,
        mbb_reserve_by_gs={"gs-fixed": 0},
        bbm_acquire_timeout_ticks=1,
        ignored_capacity_fields=(),
    )


def _decision(pair: tuple[str, str], *, visible: bool = False) -> GroundVisibilityDecision:
    return GroundVisibilityDecision(
        pair=pair,
        tenant_id="default",
        reference_body="earth",
        visible=visible,
        range_km=1234.5,
        elevation_deg=42.0 if visible else -5.0,
        azimuth_deg=180.0,
        sat_off_nadir_deg=0.0,
        observer_frame="body_local",
        reject_reason="ok" if visible else "elevation_below_min",
        rejecting_endpoint="none",
        applied_min_elevation_deg=25.0,
        applied_gs_max_range_km=None,
        applied_sat_max_range_km=None,
        applied_gs_field_of_regard_deg=None,
        applied_sat_field_of_regard_deg=None,
        applied_gs_max_tracking_rate_deg_s=None,
        applied_sat_max_tracking_rate_deg_s=None,
        applied_gs_boresight_mode=None,
        applied_sat_boresight_mode=None,
        applied_gs_terminal_profile=None,
        applied_sat_terminal_profile=None,
    )


def _reset_playback_globals() -> None:
    ome_main._time_accel = 1.0
    ome_main._seek_target = None
    ome_main._seeking = False
    ome_main._paused = False
    ome_main._epoch_id = 0
    ome_main._initial_epoch_committed = False


def test_playback_control_rejects_mutating_commands_before_initial_commit():
    async def _publish(state: str) -> None:
        published.append(state)

    commands = (
        PlaybackControlCommand(action="pause"),
        PlaybackControlCommand(action="resume"),
        PlaybackControlCommand(action="set_speed", factor=2.0),
        PlaybackControlCommand(
            action="seek",
            target_sim_time=datetime(2030, 1, 1, tzinfo=UTC),
        ),
        PlaybackControlCommand(action="get_status"),
    )

    for command in commands:
        _reset_playback_globals()
        published: list[str] = []
        reply = asyncio.run(ome_main._handle_playback_control_command(command, _publish))

        assert isinstance(reply, PlaybackControlRefused)
        assert reply.reason == "bootstrapping"
        assert published == []
        assert ome_main._epoch_id == 0
        assert ome_main._seek_target is None
        assert ome_main._seeking is False


def test_playback_control_seek_mutex_rejects_pause_but_allows_seek_retry():
    _reset_playback_globals()
    ome_main._initial_epoch_committed = True
    ome_main._seeking = True
    ome_main._epoch_id = 4
    ome_main._seek_target = datetime(2030, 1, 1, tzinfo=UTC).timestamp()
    published: list[str] = []

    async def _publish(state: str) -> None:
        published.append(state)

    pause_reply = asyncio.run(
        ome_main._handle_playback_control_command(
            PlaybackControlCommand(action="pause"),
            _publish,
        )
    )
    assert isinstance(pause_reply, PlaybackControlRefused)
    assert pause_reply.reason == "seeking"
    assert published == []
    assert ome_main._epoch_id == 4

    target = datetime(2030, 1, 2, tzinfo=UTC)
    retry_reply = asyncio.run(
        ome_main._handle_playback_control_command(
            PlaybackControlCommand(action="seek", target_sim_time=target),
            _publish,
        )
    )
    assert isinstance(retry_reply, PlaybackControlAccepted)
    assert retry_reply.state == PlaybackState(epoch_id=5, state="seeking")
    assert ome_main._paused is False
    assert published == ["seeking"]
    assert ome_main._seek_target == target.timestamp()


def test_playback_control_refuses_a_speed_outside_the_range_and_keeps_the_clock():
    _reset_playback_globals()
    ome_main._initial_epoch_committed = True

    async def _publish(state: str) -> None:
        raise AssertionError(f"a refused command published {state}")

    reply = asyncio.run(
        ome_main._handle_playback_control_command(
            PlaybackControlCommand(action="set_speed", factor=ome_main.MAX_TIME_ACCEL * 2),
            _publish,
        )
    )

    assert isinstance(reply, PlaybackControlRefused)
    assert reply.reason == "speed_out_of_range"
    assert ome_main._time_accel == 1.0
