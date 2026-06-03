# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Phase 6 local foundation proof tests.

These tests cover the deterministic/local parts of Phase 6. C-J packet-behavior
acceptance remains a real-pod run with retained evidence.
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from unittest.mock import AsyncMock, MagicMock

import yaml
from nodalarc.models.events import (
    ClockTick,
    EphemerisNodeFixed,
    PlaybackState,
    SessionEphemeris,
    VisibilityEvent,
)
from nodalarc.models.link_state import (
    AdminState,
    CarrierState,
    LinkState,
    LinkStateSnapshot,
    RoutingState,
)
from nodalarc.nats_channels import (
    link_state_snapshot_subject,
    ome_clock_subject,
    ome_visibility_subject,
    playback_state_subject,
    scheduling_checkpoint_subject,
    session_ephemeris_subject,
)
from nodalarc.platform_config import init_platform_config
from nodalarc.session_identity import require_session_run_id
from scheduler.actuation import (
    ActuationFailureClass,
    ActuationResult,
    AgentCommandResult,
    PairActuationResult,
)
from scheduler.dispatcher import ActiveLinkInfo, Dispatcher
from scheduler.pod_locator import PodLocationMap

from tests.conftest import build_segment_session_dict

BASE = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)
OLD = ("gs-multi", "sat-old")
NEW = ("gs-multi", "sat-new")
ACK_TO_ACTUAL_REFERENCE_SLA_MS = 1200.0


def _info(pair: tuple[str, str]) -> ActiveLinkInfo:
    return ActiveLinkInfo(
        interface_a="term0" if pair == OLD else "term1",
        interface_b="gnd0",
        latency_ms=1.0,
        bandwidth_mbps=100.0,
        link_type="ground",
        range_km=100.0,
        authority_sim_time=BASE,
        authority_source="phase6-test",
    )


def _dispatcher(*, now=None) -> Dispatcher:
    interface_map = {
        OLD: ("term0", "gnd0"),
        NEW: ("term1", "gnd0"),
    }
    loc = PodLocationMap()
    for pair in interface_map:
        for node_id in pair:
            loc._node_of[node_id] = "node-a"
    loc._agent_addrs["node-a"] = "127.0.0.1:50100"
    pool = MagicMock()
    d = Dispatcher(
        interface_map=interface_map,
        bandwidth_map=dict.fromkeys(interface_map, 100.0),
        pod_locator=loc,
        agent_pool=pool,
        session_id="phase6-proof",
        wiring_generation="sha256:" + "6" * 64,
        max_latency_age_s=1.0,
        gs_terminal_capacities={"gs-multi": 2},
        sat_ground_terminal_capacities={"sat-old": 1, "sat-new": 1},
        clean_kernel_audit_interval_s=60.0,
        now=now,
    )
    d._js = AsyncMock()
    d._nc = MagicMock()
    return d


def _vis(
    pair: tuple[str, str],
    *,
    sim_time: datetime,
    visible: bool,
    scheduled: bool,
    reject_reason: str,
    unscheduled_reason: str | None = None,
) -> VisibilityEvent:
    return VisibilityEvent(
        sim_time=sim_time,
        node_a=pair[0],
        node_b=pair[1],
        visible=visible,
        scheduled=scheduled,
        range_km=100.0,
        latency_ms=1.0,
        elevation_deg=50.0 if visible else 0.0,
        terminal_type="rf",
        link_type="ground",
        gs_terminal_index=0 if visible and scheduled else None,
        sat_terminal_index=0 if visible and scheduled else None,
        scheduling_state="active",
        visibility_reject_reason=reject_reason,
        unscheduled_reason=unscheduled_reason,
    )


def _ephemeris(*, epoch_id: int, sim_time: datetime) -> SessionEphemeris:
    return SessionEphemeris(
        epoch_id=epoch_id,
        sim_time=sim_time,
        epoch_unix=sim_time.timestamp(),
        nodes={
            "gs-multi": EphemerisNodeFixed(lat_deg=39.0, lon_deg=-105.0, alt_km=1.6),
            "sat-old": EphemerisNodeFixed(lat_deg=39.1, lon_deg=-105.0, alt_km=550.0),
            "sat-new": EphemerisNodeFixed(lat_deg=39.2, lon_deg=-105.0, alt_km=550.0),
        },
    )


def _snapshot(
    *, epoch_id: int, seq: int, sim_time: datetime, pairs: tuple[tuple[str, str], ...]
) -> LinkStateSnapshot:
    links = tuple(
        LinkState(
            node_a=pair[0],
            node_b=pair[1],
            interface_a="term0" if pair == OLD else "term1",
            interface_b="gnd0",
            admin=AdminState.UP,
            carrier=CarrierState.UP,
            routing=RoutingState.ADJACENT,
            range_km=100.0,
            latency_ms=1.0,
            bandwidth_mbps=100.0,
            link_type="ground",
            gs_terminal_index=0,
            sat_terminal_index=0,
            sim_time=sim_time,
        )
        for pair in pairs
    )
    return LinkStateSnapshot(
        sim_time=sim_time,
        snapshot_seq=seq,
        links=links,
        interval_s=1.0,
        epoch_id=epoch_id,
    )


def _success(pair: tuple[str, str], *, operation: str) -> ActuationResult:
    ack = ("agent-a", pair[0], "term0" if pair == OLD else "term1")
    pair_result = PairActuationResult(
        pair=pair,
        link_type="ground",
        gs_id="gs-multi",
        expected_ifaces=frozenset({ack}),
        successful_ifaces=frozenset({ack}),
        failure_class=ActuationFailureClass.NONE,
    )
    agent = AgentCommandResult(
        agent_addr="agent-a",
        operation=operation,
        requested=((pair[0], ack[2]),),
        success_acks=frozenset({ack}),
        failure_class=ActuationFailureClass.NONE,
        dirty_kernel=False,
        unknown_outcome=False,
        fence_failure=False,
        details={"operation": operation, "pair": list(pair)},
    )
    return ActuationResult(
        operation=operation,
        requested_pairs=frozenset({pair}),
        succeeded_pairs=frozenset({pair}),
        failed_pairs=frozenset(),
        pair_results={pair: pair_result},
        agent_results=(agent,),
    )


def test_p6_d_los_loss_queues_linkdown_intent_at_the_loss_tick() -> None:
    """C-D sim-tick bound: LOS loss becomes a LinkDown intent for that tick.

    This drives the production visibility/clock batching handlers. The assertion is
    not based on a sleep: the down intent is queued when the next clock tick closes
    the loss tick, and its sim_time is exactly the loss event time.
    """
    d = _dispatcher()
    t0 = BASE
    t1 = BASE + timedelta(seconds=2)

    async def drive() -> None:
        await d._handle_visibility_event(
            _vis(OLD, sim_time=t0, visible=True, scheduled=True, reject_reason="ok")
        )
        await d._handle_clock_tick_payload(
            {"sim_time": (t0 + timedelta(seconds=1)).isoformat(), "epoch_id": 0}
        )
        up_intent = d._dispatch_queue.get_nowait()
        assert OLD in up_intent.desired

        await d._handle_visibility_event(
            _vis(
                OLD,
                sim_time=t1,
                visible=False,
                scheduled=False,
                reject_reason="range_exceeded",
            )
        )
        await d._handle_clock_tick_payload(
            {"sim_time": (t1 + timedelta(seconds=1)).isoformat(), "epoch_id": 0}
        )

    asyncio.run(drive())

    down_intent = d._dispatch_queue.get_nowait()
    assert down_intent.source == "ome_event"
    assert down_intent.sim_time == t1
    assert OLD not in down_intent.desired
    assert (down_intent.sim_time - t1).total_seconds() == 0.0


def test_p6_d_linkdown_ack_is_awaited_before_actual_links_pop() -> None:
    """C-D wall-clock SLA: ACK is the boundary for `_actual_links` removal."""
    d = _dispatcher()
    d._actual_links[OLD] = _info(OLD)
    observed: dict[str, bool] = {}

    async def down(pairs, sim_iso, sim_time, nc, down_reasons):
        assert pairs == {OLD}
        observed["actual_still_present_while_rpc_inflight"] = OLD in d._actual_links
        return _success(OLD, operation="BatchLinkDown")

    d._send_batch_down = down
    start = perf_counter()
    asyncio.run(d._reconcile_links({}, None, BASE))
    elapsed_ms = (perf_counter() - start) * 1000.0

    assert observed["actual_still_present_while_rpc_inflight"] is True
    assert OLD not in d._actual_links
    assert elapsed_ms < ACK_TO_ACTUAL_REFERENCE_SLA_MS


def _drain_dispatch_queue(
    d: Dispatcher, ops: list[tuple[str, tuple[tuple[str, str], ...]]]
) -> None:
    while True:
        try:
            intent = d._dispatch_queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        asyncio.run(
            d._reconcile_links(
                intent.desired,
                None,
                intent.sim_time,
                intent.down_reasons,
                intent.forced_bbm_pairs,
            )
        )


def _run_fixed_replay(*, now_base: datetime) -> dict[str, object]:
    now = {"value": now_base}
    d = _dispatcher(now=lambda: now["value"])
    ops: list[tuple[str, tuple[tuple[str, str], ...]]] = []

    async def up(pairs, desired, sim_iso, sim_time, nc):
        ordered = tuple(sorted(pairs))
        ops.append(("up", ordered))
        pair = ordered[0]
        return _success(pair, operation="BatchLinkUp")

    async def down(pairs, sim_iso, sim_time, nc, down_reasons):
        ordered = tuple(sorted(pairs))
        ops.append(("down", ordered))
        pair = ordered[0]
        return _success(pair, operation="BatchLinkDown")

    d._send_batch_up = up
    d._send_batch_down = down

    async def drive_inputs() -> None:
        await d._handle_visibility_event(
            _vis(OLD, sim_time=BASE, visible=True, scheduled=True, reject_reason="ok")
        )
        await d._handle_clock_tick_payload(
            {"sim_time": (BASE + timedelta(seconds=1)).isoformat(), "epoch_id": 0}
        )

    asyncio.run(drive_inputs())
    _drain_dispatch_queue(d, ops)

    async def drive_handover() -> None:
        t2 = BASE + timedelta(seconds=2)
        await d._handle_visibility_event(
            _vis(OLD, sim_time=t2, visible=False, scheduled=False, reject_reason="range_exceeded")
        )
        await d._handle_visibility_event(
            _vis(NEW, sim_time=t2, visible=True, scheduled=True, reject_reason="ok")
        )
        await d._handle_clock_tick_payload(
            {"sim_time": (t2 + timedelta(seconds=1)).isoformat(), "epoch_id": 0}
        )

    now["value"] = now_base + timedelta(seconds=30)
    asyncio.run(drive_handover())
    _drain_dispatch_queue(d, ops)

    return {
        "desired": tuple(sorted(d._desired_links)),
        "actual": tuple(sorted(d._actual_links)),
        "ops": tuple(ops),
        "pending": tuple(sorted(d._pending_since)),
        "verify_count": 0,
    }


def test_p6_h_fixed_scheduler_replay_is_wall_clock_independent() -> None:
    """C-H layer 1: identical inputs replay to the same state and dispatch order.

    The two runs use different injected wall-clock bases. Equality here proves the
    scheduling/dispatch sequence is a pure function of the captured control-plane
    inputs plus ACK script, not the process wall clock.
    """
    first = _run_fixed_replay(now_base=datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC))
    second = _run_fixed_replay(now_base=datetime(2031, 1, 1, 0, 0, 0, tzinfo=UTC))

    assert first == second
    assert first["ops"] == (
        ("up", (OLD,)),
        ("down", (OLD,)),
        ("up", (NEW,)),
    )
    assert first["actual"] == (NEW,)
    assert first["desired"] == (NEW,)


def _run_seek_control_plane_replay(*, now_base: datetime) -> dict[str, object]:
    now = {"value": now_base}
    d = _dispatcher(now=lambda: now["value"])
    d._actual_links[OLD] = _info(OLD)
    ops: list[tuple[str, tuple[tuple[str, str], ...]]] = []

    async def up(pairs, desired, sim_iso, sim_time, nc):
        ordered = tuple(sorted(pairs))
        ops.append(("up", ordered))
        return _success(ordered[0], operation="BatchLinkUp")

    async def down(pairs, sim_iso, sim_time, nc, down_reasons):
        ordered = tuple(sorted(pairs))
        ops.append(("down", ordered))
        return _success(ordered[0], operation="BatchLinkDown")

    d._send_batch_up = up
    d._send_batch_down = down

    target = BASE - timedelta(minutes=5)
    stale_old_epoch_event = _vis(
        OLD,
        sim_time=BASE + timedelta(seconds=10),
        visible=True,
        scheduled=True,
        reject_reason="ok",
    )
    events = (
        ("playback", PlaybackState(epoch_id=1, state="seeking")),
        ("visibility", stale_old_epoch_event),
        ("ephemeris", _ephemeris(epoch_id=1, sim_time=target)),
        ("snapshot", _snapshot(epoch_id=1, seq=10, sim_time=target, pairs=(NEW,))),
        ("playback", PlaybackState(epoch_id=1, state="playing")),
        ("clock", {"sim_time": target.isoformat(), "epoch_id": 1}),
    )
    consumed: list[str] = []

    async def apply_events() -> None:
        for kind, payload in events:
            consumed.append(kind)
            if kind == "playback":
                await d._handle_playback_state(payload)
            elif kind == "visibility":
                await d._handle_visibility_event(payload)
            elif kind == "ephemeris":
                await d._handle_session_ephemeris(payload)
            elif kind == "snapshot":
                await d._handle_link_state_snapshot(payload)
            elif kind == "clock":
                await d._handle_clock_tick_payload(payload)
            else:  # pragma: no cover - fixture is static.
                raise AssertionError(kind)

    asyncio.run(apply_events())
    assert consumed == [kind for kind, _payload in events]
    assert d._suspended is False
    assert d._pending_visibility_events == []
    assert d._last_visibility_sim_time is None

    resume_intent = d._dispatch_queue.get_nowait()
    assert resume_intent.source == "resume"
    assert resume_intent.sim_time == target
    assert tuple(sorted(resume_intent.desired)) == (NEW,)

    asyncio.run(
        d._reconcile_links(
            resume_intent.desired,
            None,
            resume_intent.sim_time,
            resume_intent.down_reasons,
            resume_intent.forced_bbm_pairs,
        )
    )

    return {
        "consumed": tuple(consumed),
        "desired": tuple(sorted(d._desired_links)),
        "actual": tuple(sorted(d._actual_links)),
        "ops": tuple(ops),
        "pending": tuple(sorted(d._pending_since)),
        "epoch": d._expected_epoch_id,
        "snapshot_seq": d._last_snapshot_seq,
        "snapshot_epoch": d._last_snapshot_epoch_id,
        "suspended": d._suspended,
    }


def test_p6_h_control_plane_replay_consumes_seek_stream_and_resumes() -> None:
    """C-H layers 2/3: replay consumes control-plane inputs and does not stall.

    This fixture crosses a reverse seek, includes PlaybackState/SessionEphemeris/
    LinkStateSnapshot/ClockTick, and injects an old-epoch visibility event while
    suspended. Matching outputs across different wall-clock bases prove the replay
    is not accidentally using process time, while the explicit consumed/resumed
    assertions prevent a vacuous two-stalled-runs pass.
    """
    first = _run_seek_control_plane_replay(now_base=datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC))
    second = _run_seek_control_plane_replay(now_base=datetime(2031, 1, 1, 0, 0, 0, tzinfo=UTC))

    assert first == second
    assert first["consumed"] == (
        "playback",
        "visibility",
        "ephemeris",
        "snapshot",
        "playback",
        "clock",
    )
    assert first["ops"] == (("down", (OLD,)), ("up", (NEW,)))
    assert first["desired"] == (NEW,)
    assert first["actual"] == (NEW,)
    assert first["pending"] == ()
    assert first["epoch"] == 1
    assert first["snapshot_seq"] == 10
    assert first["suspended"] is False


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


def _success_many(
    pairs: set[tuple[str, str]] | frozenset[tuple[str, str]],
    *,
    operation: str,
    infos: dict[tuple[str, str], ActiveLinkInfo],
    gs_ids: frozenset[str],
) -> ActuationResult:
    pair_results: dict[tuple[str, str], PairActuationResult] = {}
    success_acks: set[tuple[str, str, str]] = set()
    requested: list[tuple[str, str]] = []
    for pair in sorted(pairs):
        info = infos[pair]
        ack = ("agent-a", pair[0], info.interface_a)
        success_acks.add(ack)
        requested.append((pair[0], info.interface_a))
        gs_id = next((node_id for node_id in pair if node_id in gs_ids), None)
        pair_results[pair] = PairActuationResult(
            pair=pair,
            link_type=info.link_type,
            gs_id=gs_id,
            expected_ifaces=frozenset({ack}),
            successful_ifaces=frozenset({ack}),
            failure_class=ActuationFailureClass.NONE,
        )
    agent = AgentCommandResult(
        agent_addr="agent-a",
        operation=operation,
        requested=tuple(requested),
        success_acks=frozenset(success_acks),
        failure_class=ActuationFailureClass.NONE,
        dirty_kernel=False,
        unknown_outcome=False,
        fence_failure=False,
        details={"operation": operation, "pair_count": len(pair_results)},
    )
    return ActuationResult(
        operation=operation,
        requested_pairs=frozenset(pairs),
        succeeded_pairs=frozenset(pairs),
        failed_pairs=frozenset(),
        pair_results=pair_results,
        agent_results=(agent,),
    )


def _reset_ome_playback_globals() -> None:
    import ome.main as ome_main

    ome_main._time_accel = 1.0
    ome_main._seek_target = None
    ome_main._seeking = False
    ome_main._paused = False
    ome_main._epoch_id = 0
    ome_main._initial_epoch_committed = False


def _demo_phase6_session_path(tmp_path: Path) -> Path:
    session_path = tmp_path / "earth-leo-quickstart.yaml"
    session_path.write_text(
        yaml.dump(
            build_segment_session_dict(
                name="earth-leo-quickstart",
                constellation="configs/constellations/demo-36.yaml",
                ground_stations="configs/ground-stations/sets/demo.yaml",
                protocol="ospf",
                orbit_propagator="j2-mean-elements",
            ),
            sort_keys=False,
        )
    )
    return session_path


def _capture_ome_seek_stream(monkeypatch, tmp_path: Path) -> tuple[str, list[tuple[str, bytes]]]:
    session_path = _demo_phase6_session_path(tmp_path)

    import nats
    import ome.event_stream as ome_event_stream
    import ome.main as ome_main
    from ome.main import _load_session_config, _run_pacing

    async def _fake_connect(*args, **kwargs):
        return _NoCheckpointNc()

    monkeypatch.setattr(nats, "connect", _fake_connect)
    init_platform_config(Path("configs/platform.yaml"))
    _reset_ome_playback_globals()

    cfg = _load_session_config(str(session_path))
    cfg = cfg._replace(
        session=cfg.session.model_copy(
            update={"session": cfg.session.session.model_copy(update={"run_id": "phase6-replay"})}
        )
    )
    session_id = require_session_run_id(cfg.session)

    real_compute_step = ome_event_stream.compute_step
    seek_target: dict[str, float] = {}

    def _compute_step_with_mid_tick_seek(*args, **kwargs):
        result = real_compute_step(*args, **kwargs)
        epoch_unix = float(args[1])
        step = int(args[2])
        if step == 1 and "unix" not in seek_target:
            target_unix = epoch_unix - 120.0
            seek_target["unix"] = target_unix
            ome_main._epoch_id += 1
            ome_main._seeking = True
            ome_main._seek_target = target_unix
            ome_main._paused = False
        return result

    monkeypatch.setattr(ome_event_stream, "compute_step", _compute_step_with_mid_tick_seek)

    shutdown = threading.Event()
    records = _StopAfterClockCountQueue(shutdown, ome_clock_subject(session_id), stop_after=2)
    _run_pacing(
        str(session_path),
        output_dir=None,
        event_queue=records,
        shutdown_event=shutdown,
        preloaded_cfg=cfg,
    )
    assert "unix" in seek_target
    return session_id, records.records


def _dispatcher_for_captured_records(
    *, session_id: str, records: list[tuple[str, bytes]], now_base: datetime
) -> Dispatcher:
    interface_map: dict[tuple[str, str], tuple[str, str]] = {}
    bandwidth_map: dict[tuple[str, str], float] = {}
    nodes: set[str] = set()
    gs_ids: set[str] = set()

    for subject, payload in records:
        if subject != link_state_snapshot_subject(session_id):
            continue
        snapshot = LinkStateSnapshot.model_validate_json(payload)
        for link in snapshot.links:
            pair = (link.node_a, link.node_b)
            interface_map[pair] = (link.interface_a, link.interface_b)
            bandwidth_map[pair] = link.bandwidth_mbps or 100.0
            nodes.update(pair)
            for node_id in pair:
                if node_id.startswith("gs-"):
                    gs_ids.add(node_id)

    loc = PodLocationMap()
    for node_id in nodes:
        loc._node_of[node_id] = "node-a"
    loc._agent_addrs["node-a"] = "127.0.0.1:50100"
    sat_ids = {node_id for node_id in nodes if node_id not in gs_ids}
    d = Dispatcher(
        interface_map=interface_map,
        bandwidth_map=bandwidth_map,
        pod_locator=loc,
        agent_pool=MagicMock(),
        session_id=session_id,
        wiring_generation="sha256:" + "6" * 64,
        max_latency_age_s=5.0,
        gs_terminal_capacities=dict.fromkeys(gs_ids, 100),
        sat_ground_terminal_capacities=dict.fromkeys(sat_ids, 100),
        clean_kernel_audit_interval_s=None,
        now=lambda: now_base,
    )
    d._js = AsyncMock()
    d._nc = MagicMock()
    return d


def _drain_dispatch_queue_with_success(
    d: Dispatcher, ops: list[tuple[str, tuple[tuple[str, str], ...]]]
) -> None:
    async def up(pairs, desired, sim_iso, sim_time, nc):
        ordered = tuple(sorted(pairs))
        ops.append(("up", ordered))
        return _success_many(
            pairs,
            operation="BatchLinkUp",
            infos={pair: desired[pair] for pair in pairs},
            gs_ids=frozenset(d._gs_capacities),
        )

    async def down(pairs, sim_iso, sim_time, nc, down_reasons):
        ordered = tuple(sorted(pairs))
        ops.append(("down", ordered))
        return _success_many(
            pairs,
            operation="BatchLinkDown",
            infos={pair: d._actual_links[pair] for pair in pairs},
            gs_ids=frozenset(d._gs_capacities),
        )

    async def latency(pairs, desired, sim_time):
        ordered = tuple(sorted(pairs))
        ops.append(("latency", ordered))
        return _success_many(
            pairs,
            operation="SetLatency",
            infos={pair: desired[pair] for pair in pairs},
            gs_ids=frozenset(d._gs_capacities),
        )

    d._send_batch_up = up
    d._send_batch_down = down
    d._send_authoritative_latency_updates = latency
    _drain_dispatch_queue(d, ops)


def _replay_ome_records(
    *, session_id: str, records: list[tuple[str, bytes]], now_base: datetime
) -> dict[str, object]:
    d = _dispatcher_for_captured_records(session_id=session_id, records=records, now_base=now_base)
    ops: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    consumed: list[str] = []

    async def apply_record(subject: str, payload: bytes) -> None:
        if subject == playback_state_subject(session_id):
            consumed.append("playback")
            await d._handle_playback_state(PlaybackState.model_validate_json(payload))
        elif subject == session_ephemeris_subject(session_id):
            consumed.append("ephemeris")
            await d._handle_session_ephemeris(SessionEphemeris.model_validate_json(payload))
        elif subject == link_state_snapshot_subject(session_id):
            consumed.append("snapshot")
            await d._handle_link_state_snapshot(LinkStateSnapshot.model_validate_json(payload))
        elif subject == ome_clock_subject(session_id):
            consumed.append("clock")
            tick = ClockTick.model_validate_json(payload)
            await d._handle_clock_tick_payload(json.loads(tick.model_dump_json()))
        elif subject == ome_visibility_subject(session_id):
            consumed.append("visibility")
            await d._handle_visibility_event(VisibilityEvent.model_validate_json(payload))
        elif subject == scheduling_checkpoint_subject(session_id):
            consumed.append("checkpoint")

    for subject, payload in records:
        asyncio.run(apply_record(subject, payload))
        if not d._suspended:
            _drain_dispatch_queue_with_success(d, ops)

    assert d._suspended is False
    assert consumed.count("clock") >= 2
    assert consumed.count("snapshot") >= 2
    assert "playback" in consumed
    return {
        "consumed": tuple(consumed),
        "desired": tuple(sorted(d._desired_links)),
        "actual": tuple(sorted(d._actual_links)),
        "ops": tuple(ops),
        "epoch": d._expected_epoch_id,
        "snapshot_seq": d._last_snapshot_seq,
        "snapshot_epoch": d._last_snapshot_epoch_id,
        "suspended": d._suspended,
    }


def test_p6_h_captured_ome_stream_replay_is_wall_clock_independent(monkeypatch, tmp_path) -> None:
    """C-H layer 2: captured OME wire stream replays deterministically.

    The fixture is produced by the real OME pacing loop from demo-36 across an
    injected epoch transition. The Scheduler consumes the serialized control-plane
    stream twice with different wall clocks and a deterministic ACK script. This
    is intentionally stronger than comparing two Scheduler helpers: it crosses
    the OME Publisher boundary, Scheduler epoch handlers, and dispatch fold.
    """
    session_id, records = _capture_ome_seek_stream(monkeypatch, tmp_path)

    first = _replay_ome_records(
        session_id=session_id,
        records=records,
        now_base=datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC),
    )
    second = _replay_ome_records(
        session_id=session_id,
        records=records,
        now_base=datetime(2031, 1, 1, 0, 0, 0, tzinfo=UTC),
    )

    assert first == second
    assert first["desired"] == first["actual"]
    assert first["ops"]
    assert first["snapshot_epoch"] == 1
    assert first["snapshot_seq"] >= 2
    assert first["suspended"] is False
