# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Scheduler operational model contracts."""

import asyncio
import itertools
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from nodalarc.models.events import OpsEvent
from nodalarc.models.scheduler_ops import (
    ActualLinkSnapshot,
    ActuationFailureClass,
    ActuationOpsDetails,
    ActuationState,
    RecoveryStatus,
    SchedulerOpsCode,
    current_actual_links,
    parse_actuation_state,
)
from nodalarc.nats_channels import (
    actual_links_subject,
    actuation_state_subject,
    actuation_state_subscribe_subject,
)
from scheduler.desired_state import ActiveLinkInfo
from scheduler.dispatch_planner import diff_link_state, interface_colliding_downs
from scheduler.dispatcher import Dispatcher
from scheduler.pod_locator import PodLocationMap
from scheduler.predecessor import (
    commanded_rows,
    footprint_rows,
    inherited_actual_links,
    inherited_station_state,
    predecessor_snapshot,
    predecessor_station_records,
)

from tests.terminal_rate_fixtures import ANY_INTERFACE_RATES


def test_parse_actuation_state_never_defaults_garbage_to_clean() -> None:
    assert parse_actuation_state("garbage") is ActuationState.UNKNOWN
    assert parse_actuation_state(None) is ActuationState.UNKNOWN
    assert parse_actuation_state(123) is ActuationState.UNKNOWN


# ----- A starting Scheduler takes over its predecessor's record -----------------------------------

SESSION = "run-takeover"
GENERATION = "sha256:" + "b" * 64
EMITTED = datetime(2026, 11, 12, 18, 0, tzinfo=UTC)
ISL = ("leo-sat-p00s00", "leo-sat-p00s01")
LONG_DELAY = ("earth-relay", "mars-relay")
STATION = "earth-us-va-ashburn-gw1"
OLD_PASS = (STATION, "leo-sat-p00s19")
NEW_PASS = (STATION, "leo-sat-p00s18")
INTERFACE_MAP = {ISL: ("isl0", "isl1"), LONG_DELAY: ("isl3", "isl0")}
STATIONS = {STATION: 1}


def _snapshot(instance: str, epoch: int, links, *, generation: str = GENERATION):
    return ActualLinkSnapshot(
        session_id=SESSION,
        wiring_generation=generation,
        scheduler_instance_id=instance,
        writer_epoch=epoch,
        hostname=f"{instance}-host",
        active_links=links,
        emitted_at=EMITTED,
    )


def _proven(interface_a: str, interface_b: str, netem: tuple[float, float]) -> ActiveLinkInfo:
    info = ActiveLinkInfo(interface_a, interface_b, 9.0, 9.0, link_type="isl", range_km=2700.0)
    info.commanded_netem_ms = netem
    return info


def test_the_current_link_record_is_the_one_published_at_the_highest_writer_epoch() -> None:
    earlier = _snapshot("sched-a", 3, [])
    latest = _snapshot("sched-b", 5, [])
    other_generation = _snapshot("sched-c", 9, [], generation="sha256:" + "c" * 64)
    for order in itertools.permutations([earlier, latest, other_generation]):
        assert (
            current_actual_links(order, session_id=SESSION, wiring_generation=GENERATION) is latest
        )
    assert (
        current_actual_links(
            [earlier, latest], session_id=SESSION, wiring_generation="sha256:" + "d" * 64
        )
        is None
    )
    with pytest.raises(ValueError, match="writer epoch 5"):
        current_actual_links(
            [latest, _snapshot("sched-x", 5, [])],
            session_id=SESSION,
            wiring_generation=GENERATION,
        )


def test_a_successor_holds_the_links_its_predecessor_proved_and_retimes_them() -> None:
    proven = {
        ISL: _proven("isl0", "isl1", (1.2, 1.3)),
        OLD_PASS: _proven("term0", "gnd0", (3.1, 3.4)),
        LONG_DELAY: _proven("isl3", "isl0", (338_000.0, 338_000.0)),
    }
    record = _snapshot("sched-b", 5, commanded_rows(proven, set(proven)))
    messages = [
        _snapshot("sched-a", 4, []).model_dump_json().encode(),
        record.model_dump_json().encode(),
        _snapshot("sched-c", 9, [], generation="sha256:" + "c" * 64).model_dump_json().encode(),
    ]
    snapshot = predecessor_snapshot(messages, session_id=SESSION, wiring_generation=GENERATION)
    assert snapshot is not None and snapshot.scheduler_instance_id == "sched-b"

    taken = inherited_actual_links(
        snapshot,
        interface_map=INTERFACE_MAP,
        gs_capacities=STATIONS,
        long_delay_pairs=frozenset({LONG_DELAY}),
    )
    # The long-delay contact is the long-delay adoption's to survey in the kernel.
    assert set(taken) == {ISL, OLD_PASS}
    for pair in taken:
        assert (taken[pair].interface_a, taken[pair].interface_b) == (
            proven[pair].interface_a,
            proven[pair].interface_b,
        )
        assert taken[pair].commanded_netem_ms == proven[pair].commanded_netem_ms
    assert taken[ISL].link_type == "isl" and taken[OLD_PASS].link_type == "ground"

    # OME now wants the ISL and the station's next pass on the same terminal.
    desired = {
        ISL: ActiveLinkInfo("isl0", "isl1", 1.25, 1.25, link_type="isl", range_km=375.0),
        NEW_PASS: ActiveLinkInfo("term0", "gnd0", 3.0, 3.0, link_type="ground", range_km=900.0),
    }
    diff = diff_link_state(taken, desired)
    assert diff.to_remove == {OLD_PASS}
    assert diff.to_add == {NEW_PASS}
    assert diff.to_update_latency == {ISL}, "a link taken over is retimed to OME's geometry"
    assert interface_colliding_downs(
        to_remove=diff.to_remove, to_add=diff.to_add, actual=taken, desired=desired
    ) == {OLD_PASS}, "the old pass leaves the station's terminal before the new pass takes it"


def test_a_link_record_that_is_not_this_sessions_refuses_the_start() -> None:
    moved = _snapshot(
        "sched-b", 5, commanded_rows({ISL: _proven("isl2", "isl1", (1.0, 1.0))}, {ISL})
    )
    with pytest.raises(RuntimeError, match="the session wires it on"):
        inherited_actual_links(
            moved, interface_map=INTERFACE_MAP, gs_capacities=STATIONS, long_delay_pairs=frozenset()
        )
    stranger = ("leo-sat-p00s00", "leo-sat-p07s07")
    unknown = _snapshot(
        "sched-b", 5, commanded_rows({stranger: _proven("isl2", "isl3", (1.0, 1.0))}, {stranger})
    )
    with pytest.raises(RuntimeError, match="neither a fixed link nor an access link"):
        inherited_actual_links(
            unknown,
            interface_map=INTERFACE_MAP,
            gs_capacities=STATIONS,
            long_delay_pairs=frozenset(),
        )


def _station_record(details: ActuationOpsDetails, code: SchedulerOpsCode) -> bytes:
    return (
        OpsEvent(
            timestamp=EMITTED,
            session_id=SESSION,
            source="scheduler",
            hostname="sched-b-host",
            level="debug",
            code=code.value,
            message="retained station state",
            details=details.model_dump(mode="json"),
        )
        .model_dump_json()
        .encode()
    )


def _station_details(
    *, gs_id: str = STATION, generation: str = GENERATION, **fields
) -> ActuationOpsDetails:
    return ActuationOpsDetails(
        session_id=SESSION,
        wiring_generation=generation,
        scheduler_instance_id="sched-b",
        hostname="sched-b-host",
        gs_id=gs_id,
        operation="RecoverableStateHeartbeat",
        failure_class=ActuationFailureClass.NONE,
        **fields,
    )


def test_a_successor_resumes_each_station_where_its_predecessor_left_it() -> None:
    exhausted = RecoveryStatus(
        verify_attempt_count=5,
        last_verify_result="failed",
        verify_exhausted=True,
        operator_action_required=True,
    )
    dirty = _station_details(
        affected_pairs=[list(NEW_PASS)],
        node_agent_results=[{"node_id": STATION, "interface_name": "term1", "verified": False}],
        actuation_state_before=ActuationState.KERNEL_DIRTY,
        actuation_state_after=ActuationState.KERNEL_DIRTY,
        recovery_status=exhausted,
        stale_links=footprint_rows({NEW_PASS: _proven("term1", "gnd2", (3.0, 3.0))}),
    )
    clean = _station_details(
        gs_id="earth-de-frankfurt-gw1",
        actuation_state_before=ActuationState.CLEAN,
        actuation_state_after=ActuationState.CLEAN,
    )
    messages = [
        _station_record(dirty, SchedulerOpsCode.KERNEL_DIRTY),
        _station_record(clean, SchedulerOpsCode.ACTUATION_CLEAN),
        _station_record(
            _station_details(gs_id="earth-jp-yamaguchi-gw1", generation="sha256:" + "c" * 64),
            SchedulerOpsCode.KERNEL_DIRTY,
        ),
    ]
    records = predecessor_station_records(
        messages, session_id=SESSION, wiring_generation=GENERATION
    )
    assert set(records) == {STATION, "earth-de-frankfurt-gw1"}

    state, stale = inherited_station_state(STATION, *records[STATION])
    assert state.state == ActuationState.KERNEL_DIRTY
    assert state.affected_pairs == frozenset({NEW_PASS})
    assert state.recovery == exhausted, "an exhausted verify still asks the operator for repair"
    assert state.node_agent_results == tuple(dirty.node_agent_results)
    assert {pair: (info.interface_a, info.interface_b) for pair, info in stale.items()} == {
        NEW_PASS: ("term1", "gnd2")
    }, "the successor proves the same stale footprint down"

    state, stale = inherited_station_state(
        "earth-de-frankfurt-gw1", *records["earth-de-frankfurt-gw1"]
    )
    assert state.state == ActuationState.CLEAN and stale == {}


def test_a_record_of_this_generation_from_an_incompatible_build_refuses_the_start() -> None:
    record = json.loads(_snapshot("sched-b", 5, []).model_dump_json())
    del record["active_links"]
    record["active_pairs"] = [list(OLD_PASS)]
    with pytest.raises(RuntimeError, match="Deploy the session again"):
        predecessor_snapshot(
            [json.dumps(record).encode()], session_id=SESSION, wiring_generation=GENERATION
        )


class _RetainedLinkStream:
    """The NODALARC_LINKS records a predecessor left, read the way a stream serves them: the last
    message per subject. It also keeps what the starting Scheduler publishes. Why a stand-in: a
    cluster cannot be made to lose one station's record while keeping the link record."""

    def __init__(self, retained: dict[str, bytes]) -> None:
        self.retained = dict(retained)
        self.published: list[tuple[str, dict]] = []

    async def stream_info(self, _stream: str, subjects_filter: str):
        prefix = subjects_filter.removesuffix(">")
        subjects = {subject: 1 for subject in self.retained if subject.startswith(prefix)}
        return SimpleNamespace(state=SimpleNamespace(subjects=subjects))

    async def get_last_msg(self, _stream: str, subject: str):
        return SimpleNamespace(data=self.retained[subject])

    async def publish(self, subject: str, payload: bytes = b"", *_args, **_kwargs) -> None:
        self.published.append((subject, json.loads(payload)))


def test_a_station_its_predecessor_left_no_record_for_starts_unproven() -> None:
    other = "earth-de-frankfurt-gw1"
    proven = {OLD_PASS: _proven("term0", "gnd0", (3.1, 3.4))}
    clean = _station_details(
        actuation_state_before=ActuationState.CLEAN, actuation_state_after=ActuationState.CLEAN
    )
    stream = _RetainedLinkStream(
        {
            actual_links_subject(SESSION, "sched-a"): _snapshot(
                "sched-a", 4, commanded_rows(proven, set(proven))
            )
            .model_dump_json()
            .encode(),
            actuation_state_subject(SESSION, STATION): _station_record(
                clean, SchedulerOpsCode.ACTUATION_CLEAN
            ),
        }
    )
    scheduler = Dispatcher(
        interface_map=INTERFACE_MAP,
        interface_rates=ANY_INTERFACE_RATES,
        pod_locator=PodLocationMap(),
        agent_pool=None,
        session_id=SESSION,
        wiring_generation=GENERATION,
        writer_epoch=5,
        max_latency_age_s=1.0,
        gs_terminal_capacities={STATION: 1, other: 1},
        gs_handover_modes={STATION: "bbm", other: "bbm"},
        sat_ground_terminal_capacities={OLD_PASS[1]: 1},
        long_delay_bounds_s={},
    )
    scheduler._js = stream

    async def start() -> None:
        await scheduler._publish_startup_actuation_roster(
            await scheduler._take_over_from_predecessor()
        )

    asyncio.run(start())

    assert set(scheduler._actual_links) == {OLD_PASS}, "the predecessor's proven pass is held"
    assert scheduler._ground_state(STATION).state == ActuationState.CLEAN
    assert scheduler._ground_state(other).state == ActuationState.KERNEL_DIRTY, (
        "a station nobody recorded is shown clean"
    )
    retained_states = {
        subject: message["details"]["actuation_state_after"]
        for subject, message in stream.published
        if subject.startswith(actuation_state_subscribe_subject(SESSION).removesuffix(">"))
    }
    assert retained_states == {
        actuation_state_subject(SESSION, STATION): "clean",
        actuation_state_subject(SESSION, other): "kernel_dirty",
    }


def test_a_successor_takes_over_the_delays_its_predecessor_last_commanded() -> None:
    stream = _RetainedLinkStream({})
    predecessor = Dispatcher(
        interface_map=INTERFACE_MAP,
        interface_rates=ANY_INTERFACE_RATES,
        pod_locator=PodLocationMap(),
        agent_pool=None,
        session_id=SESSION,
        wiring_generation=GENERATION,
        writer_epoch=4,
        max_latency_age_s=1.0,
        gs_terminal_capacities=STATIONS,
        gs_handover_modes={STATION: "bbm"},
        sat_ground_terminal_capacities={OLD_PASS[1]: 1},
        long_delay_bounds_s={},
    )
    predecessor._js = stream
    predecessor._actual_links[ISL] = _proven("isl0", "isl1", (1.2, 1.3))

    async def retime() -> None:
        await predecessor._publish_actual_links()
        before, pending = predecessor._actual_record(), frozenset(predecessor._pending_since)
        # A proven SetLatency replaces the link's record with the one carrying the new delays.
        predecessor._actual_links[ISL] = _proven("isl0", "isl1", (1.6, 1.7))
        await predecessor._publish_actual_links_if_changed(before, pending)

    asyncio.run(retime())

    retained = [
        json.dumps(message).encode()
        for subject, message in stream.published
        if subject == actual_links_subject(SESSION, predecessor._scheduler_instance_id)
    ]
    snapshot = predecessor_snapshot(retained[-1:], session_id=SESSION, wiring_generation=GENERATION)
    assert snapshot is not None
    taken = inherited_actual_links(
        snapshot, interface_map=INTERFACE_MAP, gs_capacities=STATIONS, long_delay_pairs=frozenset()
    )
    assert taken[ISL].commanded_netem_ms == (1.6, 1.7), (
        "the successor would prove the kernel against delays it no longer carries"
    )
