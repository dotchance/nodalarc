# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Phase 4 OME epoch-commit ordering contracts."""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path

import ome.event_stream as ome_event_stream
import ome.main as ome_main
import pytest
from nodalarc.models.events import (
    ClockTick,
    OpsEvent,
    PlaybackControlCommand,
    PlaybackState,
    SessionEphemeris,
)
from nodalarc.models.link_decisions import GroundLinkDecisionSnapshot, GroundPolicyAudit
from nodalarc.models.link_state import LinkStateSnapshot
from nodalarc.nats_channels import (
    ground_link_decision_snapshot_subject,
    link_state_snapshot_subject,
    ome_clock_subject,
    ome_visibility_subject,
    ops_event_subject,
    playback_state_subject,
    scheduling_checkpoint_subject,
    session_ephemeris_subject,
)
from nodalarc.platform_config import init_platform_config
from nodalarc.scheduling_checkpoint import decode_retained_scheduling_checkpoint
from nodalarc.session_identity import require_session_run_id
from ome.event_stream import StepResult
from ome.ground_allocator import GroundAllocationResult
from ome.main import _load_session_config, _run_pacing
from ome.snapshot_builder import LinkSnapshotSource
from ome.types import GroundVisibilityDecision, MbbTeardownLifecycleEvent


@pytest.fixture(autouse=True)
def _disable_lookahead(monkeypatch):
    monkeypatch.setattr(ome_main._LookAheadThread, "submit", lambda self, **_kwargs: None)


class _NoCheckpointSub:
    async def next_msg(self, timeout: float):
        raise TimeoutError

    async def unsubscribe(self) -> None:
        return None


class _NoCheckpointJs:
    async def subscribe(self, *args, **kwargs):
        return _NoCheckpointSub()


class _NoCheckpointNc:
    def jetstream(self):
        return _NoCheckpointJs()

    async def close(self) -> None:
        return None


class _RecordingQueue:
    def __init__(self, shutdown_event: threading.Event, stop_subject: str) -> None:
        self.records: list[tuple[str, bytes]] = []
        self._shutdown_event = shutdown_event
        self._stop_subject = stop_subject

    def put(self, item, timeout: float | None = None) -> None:
        self.records.append(item)
        subject, _payload = item
        if subject == self._stop_subject:
            self._shutdown_event.set()


class _StopAfterClockCountQueue:
    def __init__(
        self, shutdown_event: threading.Event, clock_subject: str, stop_after: int
    ) -> None:
        self.records: list[tuple[str, bytes]] = []
        self._shutdown_event = shutdown_event
        self._clock_subject = clock_subject
        self._stop_after = stop_after
        self._clock_count = 0

    def put(self, item, timeout: float | None = None) -> None:
        self.records.append(item)
        subject, _payload = item
        if subject == self._clock_subject:
            self._clock_count += 1
            if self._clock_count >= self._stop_after:
                self._shutdown_event.set()


class _PassiveQueue:
    def __init__(self) -> None:
        self.records: list[tuple[str, bytes]] = []

    def put(self, item, timeout: float | None = None) -> None:
        self.records.append(item)


def _policy_audit() -> GroundPolicyAudit:
    return GroundPolicyAudit(
        selection_policies={"gs-fixed": "highest-elevation"},
        selection_policy_params={"gs-fixed": {}},
        handover_policies={"gs-fixed": "hysteresis"},
        handover_policy_params={"gs-fixed": {"discount_factor": 1.15, "mask_fade_range_deg": 5.0}},
        ranking_order=("service_priority", "selection_score", "lex_pair"),
        handover_mode="bbm",
        mbb_preemption="off",
        successor_abort_policy="hard_release",
        cross_tenant_displacement="off",
        mbb_overlap_ticks=3,
        mbb_reserve=0,
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


def _fixed_step_result(
    *,
    sim_time: datetime,
    step: int,
    pair: tuple[str, str] | None = None,
    lifecycle_events=(),
) -> StepResult:
    pair = pair or ("gs-fixed", "sat-fixed")
    decision = _decision(pair, visible=True)
    allocation = GroundAllocationResult(
        associations={},
        pending_teardowns={},
        scheduled_pairs=frozenset(),
        unscheduled_pairs=(),
        policy_audit=_policy_audit(),
        allocation_events=(),
        lifecycle_events=tuple(lifecycle_events),
    )
    return StepResult(
        events=[],
        positions={},
        isl_scheduled={},
        isl_feasibility={},
        isl_links={},
        ground_allocation=allocation,
        ground_decisions={pair: decision},
        link_snapshot_source=LinkSnapshotSource(
            isl_state={},
            ground_state={pair: (True, False, "active")},
            associations={},
            pending_teardowns={},
            propagated_states={},
        ),
        propagated_states={},
        sim_time=sim_time,
        sim_time_unix=sim_time.timestamp(),
        step=step,
    )


def _phase4_cfg(session_path: Path, run_id: str):
    cfg = _load_session_config(str(session_path))
    session = cfg.session.model_copy(
        update={"session": cfg.session.session.model_copy(update={"run_id": run_id})}
    )
    return cfg._replace(session=session)


def _reset_playback_globals() -> None:
    ome_main._time_accel = 1.0
    ome_main._seek_target = None
    ome_main._seeking = False
    ome_main._paused = False
    ome_main._epoch_id = 0
    ome_main._initial_epoch_committed = False


def test_initial_epoch_publishes_step0_snapshot_before_playing_and_clock(monkeypatch):
    session_path = Path("configs/sessions/demo-36-ospf.yaml")
    if not session_path.exists():
        pytest.skip("demo-36-ospf.yaml not available")

    import nats

    async def _fake_connect(*args, **kwargs):
        return _NoCheckpointNc()

    monkeypatch.setattr(nats, "connect", _fake_connect)
    init_platform_config(Path("configs/platform.yaml"))

    cfg = _phase4_cfg(session_path, "phase4-ordering")
    session_id = require_session_run_id(cfg.session)

    shutdown = threading.Event()
    records = _RecordingQueue(shutdown, ome_clock_subject(session_id))

    _run_pacing(
        str(session_path),
        output_dir=None,
        event_queue=records,
        shutdown_event=shutdown,
        preloaded_cfg=cfg,
    )

    subjects = [subject for subject, _payload in records.records]
    eph_subject = session_ephemeris_subject(session_id)
    state_subject = link_state_snapshot_subject(session_id)
    decision_subject = ground_link_decision_snapshot_subject(session_id)
    checkpoint_subject = scheduling_checkpoint_subject(session_id)
    playback_subject = playback_state_subject(session_id)
    clock_subject = ome_clock_subject(session_id)

    assert subjects[:6] == [
        eph_subject,
        state_subject,
        decision_subject,
        checkpoint_subject,
        playback_subject,
        clock_subject,
    ]
    assert ome_visibility_subject(session_id) not in subjects[:6]

    eph = SessionEphemeris.model_validate_json(records.records[0][1])
    snapshot = LinkStateSnapshot.model_validate_json(records.records[1][1])
    decisions = GroundLinkDecisionSnapshot.model_validate_json(records.records[2][1])
    checkpoint = decode_retained_scheduling_checkpoint(records.records[3][1])
    playback = PlaybackState.model_validate_json(records.records[4][1])

    assert checkpoint is not None
    assert (
        eph.epoch_id
        == snapshot.epoch_id
        == decisions.epoch_id
        == checkpoint.epoch_id
        == playback.epoch_id
    )
    assert snapshot.snapshot_seq == decisions.snapshot_seq == checkpoint.snapshot_seq
    assert snapshot.sim_time == decisions.sim_time == checkpoint.sim_time
    assert snapshot.links
    assert decisions.decisions
    assert playback.state == "playing"


def test_seek_abandons_inflight_old_tick_and_commits_step0_snapshot(monkeypatch):
    session_path = Path("configs/sessions/demo-36-ospf.yaml")
    if not session_path.exists():
        pytest.skip("demo-36-ospf.yaml not available")

    import nats

    async def _fake_connect(*args, **kwargs):
        return _NoCheckpointNc()

    monkeypatch.setattr(nats, "connect", _fake_connect)
    init_platform_config(Path("configs/platform.yaml"))

    cfg = _phase4_cfg(session_path, "phase4-seek")
    session_id = require_session_run_id(cfg.session)

    real_compute_step = ome_event_stream.compute_step
    seek_target: dict[str, float] = {}

    def _compute_step_with_mid_tick_seek(*args, **kwargs):
        result = real_compute_step(*args, **kwargs)
        epoch_unix = float(args[1])
        step = int(args[2])
        if step == 1 and "unix" not in seek_target:
            # Simulate the playback-control thread accepting a reverse seek
            # while the old tick compute is completing. No old-epoch facts may
            # publish after this point.
            target_unix = epoch_unix - 120.0
            seek_target["unix"] = target_unix
            ome_main._epoch_id += 1
            ome_main._seeking = True
            ome_main._seek_target = target_unix
            ome_main._paused = False
        return result

    monkeypatch.setattr(ome_event_stream, "compute_step", _compute_step_with_mid_tick_seek)

    shutdown = threading.Event()
    clock_subject = ome_clock_subject(session_id)
    records = _StopAfterClockCountQueue(shutdown, clock_subject, stop_after=2)

    _run_pacing(
        str(session_path),
        output_dir=None,
        event_queue=records,
        shutdown_event=shutdown,
        preloaded_cfg=cfg,
    )

    assert "unix" in seek_target

    subjects = [subject for subject, _payload in records.records]
    eph_subject = session_ephemeris_subject(session_id)
    state_subject = link_state_snapshot_subject(session_id)
    decision_subject = ground_link_decision_snapshot_subject(session_id)
    checkpoint_subject = scheduling_checkpoint_subject(session_id)
    playback_subject = playback_state_subject(session_id)
    visibility_subject = ome_visibility_subject(session_id)

    clock_indexes = [index for index, subject in enumerate(subjects) if subject == clock_subject]
    assert len(clock_indexes) == 2
    first_clock_index, second_clock_index = clock_indexes

    assert subjects[first_clock_index + 1 : second_clock_index + 1] == [
        eph_subject,
        state_subject,
        decision_subject,
        checkpoint_subject,
        playback_subject,
        clock_subject,
    ]
    assert visibility_subject not in subjects[first_clock_index + 1 : second_clock_index + 1]

    initial_snapshot = LinkStateSnapshot.model_validate_json(records.records[1][1])
    snapshot = LinkStateSnapshot.model_validate_json(records.records[first_clock_index + 2][1])
    decisions = GroundLinkDecisionSnapshot.model_validate_json(
        records.records[first_clock_index + 3][1]
    )
    checkpoint = decode_retained_scheduling_checkpoint(records.records[first_clock_index + 4][1])
    playback = PlaybackState.model_validate_json(records.records[first_clock_index + 5][1])
    clock = ClockTick.model_validate_json(records.records[second_clock_index][1])
    expected_sim_time = datetime.fromtimestamp(seek_target["unix"], UTC)

    assert checkpoint is not None
    assert (
        snapshot.epoch_id
        == decisions.epoch_id
        == checkpoint.epoch_id
        == playback.epoch_id
        == clock.epoch_id
        == 1
    )
    assert snapshot.snapshot_seq == initial_snapshot.snapshot_seq + 1
    assert snapshot.snapshot_seq == decisions.snapshot_seq == checkpoint.snapshot_seq
    assert (
        snapshot.sim_time
        == decisions.sim_time
        == checkpoint.sim_time
        == clock.sim_time
        == expected_sim_time
    )
    assert snapshot.links
    assert decisions.decisions
    assert playback.state == "playing"


def test_initial_epoch_ordering_oracle_uses_fixed_step_result(monkeypatch):
    session_path = Path("configs/sessions/demo-36-ospf.yaml")
    if not session_path.exists():
        pytest.skip("demo-36-ospf.yaml not available")

    import nats

    async def _fake_connect(*args, **kwargs):
        return _NoCheckpointNc()

    monkeypatch.setattr(nats, "connect", _fake_connect)
    init_platform_config(Path("configs/platform.yaml"))

    cfg = _phase4_cfg(session_path, "phase4-ordering-oracle")
    session_id = require_session_run_id(cfg.session)
    fixed_time = datetime(2030, 1, 1, 0, 0, 0, tzinfo=UTC)
    fixed_result = _fixed_step_result(sim_time=fixed_time, step=0)
    calls: list[tuple[float, int]] = []
    shutdown = threading.Event()

    def _fixed_compute_step(_ctx, epoch_unix, step, *_args, **_kwargs):
        calls.append((float(epoch_unix), int(step)))
        shutdown.set()
        return fixed_result

    monkeypatch.setattr(ome_event_stream, "compute_step", _fixed_compute_step)
    monkeypatch.setattr(ome_main._LookAheadThread, "submit", lambda self, **_kwargs: None)

    records = _PassiveQueue()

    _run_pacing(
        str(session_path),
        output_dir=None,
        event_queue=records,
        shutdown_event=shutdown,
        preloaded_cfg=cfg,
    )

    assert len(calls) == 1
    assert calls[0][1] == 0

    subjects = [subject for subject, _payload in records.records]
    state_subject = link_state_snapshot_subject(session_id)
    decision_subject = ground_link_decision_snapshot_subject(session_id)
    checkpoint_subject = scheduling_checkpoint_subject(session_id)
    playback_subject = playback_state_subject(session_id)
    clock_subject = ome_clock_subject(session_id)

    assert subjects[:6] == [
        session_ephemeris_subject(session_id),
        state_subject,
        decision_subject,
        checkpoint_subject,
        playback_subject,
        clock_subject,
    ]
    assert ome_visibility_subject(session_id) not in subjects[:6]

    snapshot = LinkStateSnapshot.model_validate_json(records.records[1][1])
    decisions = GroundLinkDecisionSnapshot.model_validate_json(records.records[2][1])
    playback = PlaybackState.model_validate_json(records.records[4][1])
    clock = ClockTick.model_validate_json(records.records[5][1])

    assert snapshot.sim_time == decisions.sim_time == clock.sim_time == fixed_time
    assert snapshot.snapshot_seq == decisions.snapshot_seq == 1
    assert [(link.node_a, link.node_b, link.carrier.value) for link in snapshot.links] == [
        ("gs-fixed", "sat-fixed", "LOWERLAYERDOWN")
    ]
    assert [(decision.pair, decision.visible) for decision in decisions.decisions] == [
        (("gs-fixed", "sat-fixed"), True)
    ]
    assert playback.state == "playing"


def test_initial_epoch_lifecycle_event_uses_ops_enqueue_after_snapshot(monkeypatch):
    session_path = Path("configs/sessions/demo-36-ospf.yaml")
    if not session_path.exists():
        pytest.skip("demo-36-ospf.yaml not available")

    import nats

    async def _fake_connect(*args, **kwargs):
        return _NoCheckpointNc()

    monkeypatch.setattr(nats, "connect", _fake_connect)
    init_platform_config(Path("configs/platform.yaml"))

    cfg = _phase4_cfg(session_path, "phase6-lifecycle-enqueue")
    session_id = require_session_run_id(cfg.session)
    sim_time = datetime(2030, 1, 1, 0, 0, 0, tzinfo=UTC)
    old_pair = ("gs-fixed", "sat-old")
    successor_pair = ("gs-fixed", "sat-fixed")
    lifecycle = MbbTeardownLifecycleEvent(
        category="teardown_completed",
        old_pair=old_pair,
        successor_pair=successor_pair,
        gs_id="gs-fixed",
        message="MBB teardown completed",
        source_allocation_event_category="teardown_completed",
        authority_before={
            "old_pair": {
                "pair": list(old_pair),
                "scheduled": True,
                "pending_teardown": True,
                "visible": True,
                "terminal_indices": [0, 0],
            },
            "successor_pair": {
                "pair": list(successor_pair),
                "scheduled": True,
                "pending_teardown": False,
                "visible": True,
                "terminal_indices": [1, 0],
            },
        },
    )
    fixed_result = _fixed_step_result(
        sim_time=sim_time,
        step=0,
        pair=successor_pair,
        lifecycle_events=(lifecycle,),
    )
    shutdown = threading.Event()

    def _fixed_compute_step(_ctx, _epoch_unix, _step, *_args, **_kwargs):
        shutdown.set()
        return fixed_result

    monkeypatch.setattr(ome_event_stream, "compute_step", _fixed_compute_step)
    records = _PassiveQueue()

    _run_pacing(
        str(session_path),
        output_dir=None,
        event_queue=records,
        shutdown_event=shutdown,
        preloaded_cfg=cfg,
    )

    subjects = [subject for subject, _payload in records.records]
    lifecycle_subject = ops_event_subject(session_id, "ome", "MBB_TEARDOWN_TERMINAL")
    snapshot_index = subjects.index(link_state_snapshot_subject(session_id))
    lifecycle_index = subjects.index(lifecycle_subject)
    checkpoint_index = subjects.index(scheduling_checkpoint_subject(session_id))

    assert snapshot_index < lifecycle_index < checkpoint_index
    event = OpsEvent.model_validate_json(records.records[lifecycle_index][1])
    assert event.source == "ome"
    assert event.code == "MBB_TEARDOWN_TERMINAL"
    assert event.details["terminal_outcome"] == "teardown_completed"
    assert event.details["snapshot_seq"] == 1
    assert event.details["authority_before"]["old_pair"]["pending_teardown"] is True


def test_seek_step0_compute_failure_logs_epoch_and_target_without_new_snapshot(monkeypatch, caplog):
    session_path = Path("configs/sessions/demo-36-ospf.yaml")
    if not session_path.exists():
        pytest.skip("demo-36-ospf.yaml not available")

    import nats

    async def _fake_connect(*args, **kwargs):
        return _NoCheckpointNc()

    monkeypatch.setattr(nats, "connect", _fake_connect)
    monkeypatch.setattr(ome_main.time, "sleep", lambda _seconds: None)
    init_platform_config(Path("configs/platform.yaml"))

    cfg = _phase4_cfg(session_path, "phase4-seek-failure")
    session_id = require_session_run_id(cfg.session)
    seek_target: dict[str, float] = {}

    def _compute_step_with_failed_seek(_ctx, epoch_unix, step, step_seconds, *_args, **_kwargs):
        epoch_unix = float(epoch_unix)
        step = int(step)
        if step == 0 and ome_main._seeking:
            raise RuntimeError("synthetic seek failure")
        sim_time = datetime.fromtimestamp(epoch_unix + step * int(step_seconds), UTC)
        if step == 1 and "unix" not in seek_target:
            target_unix = epoch_unix - 300.0
            seek_target["unix"] = target_unix
            ome_main._epoch_id += 1
            ome_main._seeking = True
            ome_main._seek_target = target_unix
            ome_main._paused = False
        return _fixed_step_result(sim_time=sim_time, step=step)

    monkeypatch.setattr(ome_event_stream, "compute_step", _compute_step_with_failed_seek)

    shutdown = threading.Event()
    records = _PassiveQueue()

    with caplog.at_level(logging.ERROR):
        _run_pacing(
            str(session_path),
            output_dir=None,
            event_queue=records,
            shutdown_event=shutdown,
            preloaded_cfg=cfg,
        )

    assert "unix" in seek_target
    structured = [
        record
        for record in caplog.records
        if getattr(record, "code", None) == "SEEK_STEP0_COMPUTE_FAILED"
    ]
    assert len(structured) == 1
    details = structured[0].__dict__["details"]
    assert details["epoch_id"] == 1
    assert (
        details["target_master_sim_time"]
        == datetime.fromtimestamp(seek_target["unix"], UTC).isoformat()
    )
    assert details["exception_type"] == "RuntimeError"
    assert details["exception"] == "synthetic seek failure"

    state_subject = link_state_snapshot_subject(session_id)
    snapshots = [
        LinkStateSnapshot.model_validate_json(payload)
        for subject, payload in records.records
        if subject == state_subject
    ]
    assert [snapshot.epoch_id for snapshot in snapshots] == [0]
    assert shutdown.is_set()
    assert ome_main._seeking is True


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
    )

    for command in commands:
        _reset_playback_globals()
        published: list[str] = []
        reply = asyncio.run(ome_main._handle_playback_control_command(command, _publish))

        assert reply == {
            "error": "session bootstrapping; retry after ready",
            "state": "bootstrapping",
            "paused": False,
            "speed": 1.0,
            "epoch_id": 0,
        }
        assert published == []
        assert ome_main._epoch_id == 0
        assert ome_main._seek_target is None
        assert ome_main._seeking is False

    status = asyncio.run(
        ome_main._handle_playback_control_command(
            PlaybackControlCommand(action="get_status"),
            _publish,
        )
    )
    assert status["state"] == "bootstrapping"
    assert published == []


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
    assert pause_reply["state"] == "seeking"
    assert pause_reply["error"] == "cannot pause during seek (epoch_id=4)"
    assert published == []
    assert ome_main._epoch_id == 4

    target = datetime(2030, 1, 2, tzinfo=UTC)
    retry_reply = asyncio.run(
        ome_main._handle_playback_control_command(
            PlaybackControlCommand(action="seek", target_sim_time=target),
            _publish,
        )
    )
    assert retry_reply["state"] == "seeking"
    assert retry_reply["epoch_id"] == 5
    assert retry_reply["paused"] is False
    assert published == ["seeking"]
    assert ome_main._seek_target == target.timestamp()
