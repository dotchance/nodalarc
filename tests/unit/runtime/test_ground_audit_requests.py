# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""The proof a station's kernel audit asks for, pass by pass.

The Scheduler's periodic clean-state audit of a station and the re-check of a station that is not
clean both prove the station's passes through one function. A local pass is proven against the
local ground layout, a delay queue on each pod interface. A long-delay pass has no queue there:
its ends carry the contact on their host-side veths, so it is proven by the long-delay inventory.
The requests are checked against the Node Agent's own command contract, the same check a Node
Agent applies before it reads the kernel.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from nodalarc.models.resolved_session import InterfaceRates
from nodalarc.proto import node_agent_pb2
from node_agent.command_contract import (
    RuntimeFence,
    WriterEpochFloor,
    validate_kernel_inventory_request,
)
from scheduler.contact_drain import start_drain
from scheduler.desired_state import ActiveLinkInfo, Contact
from scheduler.dispatch_actuator import ground_inventory_requests
from scheduler.pod_locator import PodLocationMap

STATION = "dss-43"
LONG_DELAY = (STATION, "mars-relay")
LOCAL = (STATION, "lunar-relay")
FENCE = RuntimeFence(
    session_id="session-a", wiring_generation="gen-1", writer_floor=WriterEpochFloor(lambda: None)
)
RATES = {
    (STATION, "term0"): InterfaceRates(transmit_mbps=0.1, receive_mbps=6.0),
    (STATION, "term1"): InterfaceRates(transmit_mbps=2.0, receive_mbps=2.0),
    ("mars-relay", "gnd0"): InterfaceRates(transmit_mbps=1.0, receive_mbps=2.0),
    ("lunar-relay", "gnd0"): InterfaceRates(transmit_mbps=2.0, receive_mbps=2.0),
}


def _locator() -> PodLocationMap:
    located = PodLocationMap()
    for node_id in (STATION, "mars-relay", "lunar-relay"):
        located._node_of[node_id] = "node01"
    located._agent_addrs["node01"] = "node01-agent"
    return located


def _long_delay_pass() -> ActiveLinkInfo:
    return ActiveLinkInfo(
        interface_a="term0",
        interface_b="gnd0",
        latency_a_to_b_ms=338_278.0,
        latency_b_to_a_ms=338_276.0,
        link_type="ground",
        range_km=1.0e8,
        contact=Contact(epoch_id=0, opened_at=datetime(2027, 2, 19, 11, 40, tzinfo=UTC)),
        commanded_netem_ms=(338_278.0, 338_276.0),
    )


def _local_pass() -> ActiveLinkInfo:
    return ActiveLinkInfo(
        interface_a="term1",
        interface_b="gnd0",
        latency_a_to_b_ms=1_281.0,
        latency_b_to_a_ms=1_281.0,
        link_type="ground",
        range_km=3.84e5,
        commanded_netem_ms=(1_281.0, 1_281.0),
    )


def _plan(expected_up: dict[tuple[str, str], ActiveLinkInfo]):
    return ground_inventory_requests(
        gs_id=STATION,
        expected_up=expected_up,
        expected_down={},
        locator=_locator(),
        gs_capacities={STATION: 2},
        interface_rates=RATES,
    )


def _request(gs_id: str, entries) -> node_agent_pb2.KernelInventoryRequest:
    return node_agent_pb2.KernelInventoryRequest(
        envelope=node_agent_pb2.CommandEnvelope(
            operation_id="op-1",
            session_id=FENCE.session_id,
            wiring_generation=FENCE.wiring_generation,
            operation_kind="KernelInventory",
            writer_epoch=1,
        ),
        target_sim_time="2027-02-19T11:41:00+00:00",
        gs_id=gs_id,
        entries=list(entries),
    )


def test_a_long_delay_pass_is_proven_by_the_long_delay_inventory_and_a_local_pass_locally():
    plan = _plan({LONG_DELAY: _long_delay_pass(), LOCAL: _local_pass()})
    by_scope = {gs_id: entries for _agent, gs_id, entries in plan.requests}
    assert set(by_scope) == {STATION, ""}

    # The local pass: one station-scoped entry wiring both ends, against the local layout.
    (local,) = by_scope[STATION]
    assert (local.node_id, local.interface_name, local.peer_node_id) == (
        STATION,
        "term1",
        "lunar-relay",
    )
    assert not local.contact_id

    # The long-delay pass: one entry per end, each naming the contact it must carry, its
    # admission open, the received delay, and the peer's rates that size its receive queue.
    long_delay = {entry.node_id: entry for entry in by_scope[""]}
    assert set(long_delay) == set(LONG_DELAY)
    station, relay = long_delay[STATION], long_delay["mars-relay"]
    for entry in (station, relay):
        assert entry.contact_id == "0@2027-02-19T11:40:00.000000Z"
        assert entry.expected_admin_up and not entry.expected_admission_stopped
    assert station.receive_latency_ms == 338_276.0
    assert relay.receive_latency_ms == 338_278.0
    assert (station.peer_rates.transmit_mbps, relay.peer_rates.transmit_mbps) == (1.0, 0.1)

    # Both requests pass the contract the Node Agent applies before it reads the kernel.
    for gs_id, entries in by_scope.items():
        validate_kernel_inventory_request(_request(gs_id, entries), fence=FENCE)


def test_a_draining_long_delay_pass_is_proven_at_the_ends_still_draining_with_admission_stopped():
    info = _long_delay_pass()
    info.drain = start_drain(
        LONG_DELAY,
        info,
        stopped_mono=100.0,
        stopped_wall=datetime(2026, 11, 6, 2, 0, tzinfo=UTC),
        reason="vis_lost",
    )
    # The relay's end has closed; only the station still drains.
    info.drain = replace(
        info.drain, ends=tuple(end for end in info.drain.ends if end.node_id == STATION)
    )
    plan = _plan({LONG_DELAY: info})
    ((_agent, gs_id, entries),) = plan.requests
    assert gs_id == ""
    (entry,) = entries
    assert entry.node_id == STATION
    assert entry.expected_admission_stopped
    validate_kernel_inventory_request(_request(gs_id, entries), fence=FENCE)
