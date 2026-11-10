# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""OME epoch-commit ordering contracts."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import ome.main as ome_main
from nodalarc.models.events import (
    PlaybackControlAccepted,
    PlaybackControlCommand,
    PlaybackControlRefused,
)
from nodalarc.models.link_decisions import GroundPolicyAudit
from nodalarc.physics.coverage import tick_coverage_gap
from ome.types import GroundVisibilityDecision

from tests.physics_fixtures import physics_for


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


_EARTH_PHYSICS = physics_for("earth")


def _reset_playback_globals() -> None:
    """The playback state of a loaded Earth session with no light time and no lookahead, checked
    against the shipped data as the pacing thread checks it."""
    ome_main._time_accel = 1.0
    ome_main._seek_target = None
    ome_main._seeking = False
    ome_main._paused = False
    ome_main._epoch_id = 0
    ome_main._initial_epoch_committed = False
    ome_main._pause_reason = None
    ome_main._coverage_limit = None
    ome_main._coverage_gap_at = lambda sim_time_unix: tick_coverage_gap(
        _EARTH_PHYSICS, sim_time_unix, light_time_bound_s=0.0, lookahead_s=0.0
    )
    # The session clock started playing at 1x: the first regime of OME's record.
    ome_main._pacing_degraded_now = False
    ome_main._time_regimes.clear()
    ome_main._record_time_regime("start", state="playing")


def test_playback_control_rejects_mutating_commands_before_initial_commit():
    async def _publish() -> None:
        published.append(ome_main._playback_state(ome_main._epoch_id).state)

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

    async def _publish() -> None:
        published.append(ome_main._playback_state(ome_main._epoch_id).state)

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
    assert (retry_reply.state.epoch_id, retry_reply.state.state) == (5, "seeking")
    assert retry_reply.state.time_regimes[-1].cause == "seek"
    assert ome_main._paused is False
    assert published == ["seeking"]
    assert ome_main._seek_target == target.timestamp()


def test_playback_control_refuses_a_speed_outside_the_range_and_keeps_the_clock():
    _reset_playback_globals()
    ome_main._initial_epoch_committed = True

    async def _publish() -> None:
        raise AssertionError("a refused command published a playback state")

    reply = asyncio.run(
        ome_main._handle_playback_control_command(
            PlaybackControlCommand(action="set_speed", factor=ome_main.MAX_TIME_ACCEL * 2),
            _publish,
        )
    )

    assert isinstance(reply, PlaybackControlRefused)
    assert reply.reason == "speed_out_of_range"
    assert ome_main._time_accel == 1.0


def test_a_seek_past_the_data_is_refused_before_anything_changes():
    _reset_playback_globals()
    ome_main._initial_epoch_committed = True
    ome_main._epoch_id = 2
    published: list[str] = []

    async def _publish() -> None:
        published.append(ome_main._playback_state(ome_main._epoch_id).state)

    reply = asyncio.run(
        ome_main._handle_playback_control_command(
            PlaybackControlCommand(action="seek", target_sim_time=datetime(2200, 1, 1, tzinfo=UTC)),
            _publish,
        )
    )

    assert isinstance(reply, PlaybackControlRefused)
    assert reply.reason == "ephemeris_coverage"
    assert reply.coverage is not None
    assert reply.coverage.body_id == "earth"
    # Earth's data ends 2126-11-03T00:00Z; a tick reads 6 h 10 min ahead (to within float rounding).
    until = datetime(2126, 11, 2, 17, 50, tzinfo=UTC)
    assert abs((reply.coverage.usable_until - until).total_seconds()) < 1e-3
    assert published == []
    assert (ome_main._epoch_id, ome_main._seeking, ome_main._seek_target) == (2, False, None)


def test_a_clock_stopped_at_the_end_of_the_data_resumes_only_through_a_seek_back():
    _reset_playback_globals()
    ome_main._initial_epoch_committed = True
    end_gap = ome_main._coverage_gap_at(datetime(2126, 11, 3, tzinfo=UTC).timestamp())
    assert end_gap is not None
    ome_main._pause_reason = "ephemeris_coverage_ended"
    ome_main._coverage_limit = ome_main._limit_of(end_gap)
    ome_main._paused = True
    # The pacing thread records the stop in OME's time-control record as it pauses.
    ome_main._record_time_regime("coverage_pause", state="paused")
    published: list[str] = []

    async def _publish() -> None:
        published.append(ome_main._playback_state(ome_main._epoch_id).state)

    resumed = asyncio.run(
        ome_main._handle_playback_control_command(PlaybackControlCommand(action="resume"), _publish)
    )
    assert isinstance(resumed, PlaybackControlRefused)
    assert resumed.reason == "ephemeris_coverage_ended"
    assert ome_main._paused is True
    assert published == []

    status = asyncio.run(
        ome_main._handle_playback_control_command(
            PlaybackControlCommand(action="get_status"), _publish
        )
    )
    assert isinstance(status, PlaybackControlAccepted)
    assert status.state.reason == "ephemeris_coverage_ended"
    assert status.state.coverage == ome_main._limit_of(end_gap)

    back = asyncio.run(
        ome_main._handle_playback_control_command(
            PlaybackControlCommand(action="seek", target_sim_time=datetime(2126, 1, 1, tzinfo=UTC)),
            _publish,
        )
    )
    assert isinstance(back, PlaybackControlAccepted)
    assert (back.state.epoch_id, back.state.state) == (1, "seeking")
    assert (ome_main._paused, ome_main._pause_reason, ome_main._coverage_limit) == (
        False,
        None,
        None,
    )


def test_a_seek_the_pacing_thread_takes_during_its_publish_is_still_accepted():
    """The pacing thread may take a seek while the handler awaits the "seeking" publish. The
    command was applied, so the reply accepts it."""
    _reset_playback_globals()
    ome_main._initial_epoch_committed = True
    target = datetime(2026, 6, 8, 5, tzinfo=UTC)

    async def _publish_while_the_pacing_thread_takes_the_seek() -> None:
        ome_main._seek_target = None

    reply = asyncio.run(
        ome_main._handle_playback_control_command(
            PlaybackControlCommand(action="seek", target_sim_time=target),
            _publish_while_the_pacing_thread_takes_the_seek,
        )
    )

    assert isinstance(reply, PlaybackControlAccepted)
    assert reply.state.epoch_id == 1
