"""NodalArc never reports a link up when the Node Agent could not prove the kernel state.

Why a stand-in: the Node Agent answers `dirty_kernel` when a kernel write or its proof failed part
way. A cluster cannot be put in that state on demand. The stand-in is the Node Agent's reply, built
from the real protobuf messages; the Scheduler under test is the real one, driven the way production
drives it: an OME event, the next clock tick, the dispatch worker.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from nodalarc.models.events import VisibilityEvent
from nodalarc.models.scheduler_ops import SchedulerOpsCode
from nodalarc.nats_channels import actual_links_subscribe_subject, link_up_subject
from nodalarc.proto import node_agent_pb2
from scheduler.dispatcher import Dispatcher
from scheduler.pod_locator import PodLocationMap

from tests.terminal_rate_fixtures import ANY_INTERFACE_RATES

PAIR = ("sat-p00s00", "sat-p00s01")
SESSION = "run-unproven-kernel"
SIM_TIME = datetime(2026, 1, 1, tzinfo=UTC)


class _NodeAgent:
    """Answers every BatchLinkUp the way a Node Agent does after a failed kernel proof."""

    def __init__(self) -> None:
        self.link_up_requests = 0

    async def async_batch_link_up(self, request, **_kwargs):
        self.link_up_requests += 1
        return node_agent_pb2.BatchLinkUpResponse(
            success=False,
            dirty_kernel=True,
            error_code=node_agent_pb2.NODE_AGENT_KERNEL_PROOF_FAILED,
            error_message="netem delay did not read back",
            interface_results=[
                node_agent_pb2.InterfaceResult(
                    node_id=interface.node_id,
                    interface_name=interface.interface_name,
                    success=False,
                    verified=False,
                    dirty_kernel=True,
                    error_code=node_agent_pb2.NODE_AGENT_KERNEL_PROOF_FAILED,
                    error_message="netem delay did not read back",
                )
                for interface in request.interfaces
            ],
        )


class _AgentPool:
    def __init__(self, agent: _NodeAgent) -> None:
        self._agent = agent

    def get_stub(self, _address: str) -> _NodeAgent:
        return self._agent


class _Published:
    """Records what the Scheduler publishes: the events the page and VS-API read."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, dict]] = []

    async def publish(self, subject: str, payload: bytes = b"", *_args, **_kwargs) -> None:
        self.messages.append((subject, json.loads(payload) if payload else {}))


def _scheduler(agent: _NodeAgent, published: _Published) -> Dispatcher:
    located = PodLocationMap()
    for node_id in PAIR:
        located._node_of[node_id] = "server-a"
    located._agent_addrs["server-a"] = "server-a-agent"
    scheduler = Dispatcher(
        interface_map={PAIR: ("isl0", "isl1")},
        interface_rates=ANY_INTERFACE_RATES,
        pod_locator=located,
        agent_pool=_AgentPool(agent),
        session_id=SESSION,
        wiring_generation="sha256:" + "a" * 64,
        writer_epoch=1,
        max_latency_age_s=1.0,
        # The session also declares one ground station; it plays no part here.
        gs_terminal_capacities={"gs-a": 1},
        gs_handover_modes={"gs-a": "bbm"},
        sat_ground_terminal_capacities={PAIR[0]: 1},
    )
    scheduler._js = published
    scheduler._nc = published
    return scheduler


def _link_scheduled() -> VisibilityEvent:
    return VisibilityEvent(
        sim_time=SIM_TIME,
        node_a=PAIR[0],
        node_b=PAIR[1],
        visible=True,
        scheduled=True,
        range_km=500.0,
        latency_ms=1.6678204759907602,
        elevation_deg=45.0,
        terminal_type="optical",
        link_type="isl",
        visibility_reject_reason="ok",
        unscheduled_reason=None,
    )


async def _ome_schedules_the_link(scheduler: Dispatcher) -> None:
    await scheduler._handle_visibility_event(_link_scheduled())
    await scheduler._handle_clock_tick_payload(
        {
            "epoch_id": scheduler._expected_epoch_id,
            "sim_time": (SIM_TIME + timedelta(seconds=1)).isoformat(),
        }
    )
    scheduler._running = True
    await scheduler._dispatch_worker(scheduler._nc)


def test_a_link_the_node_agent_could_not_prove_is_never_reported_up_and_dispatch_halts() -> None:
    agent, published = _NodeAgent(), _Published()
    scheduler = _scheduler(agent, published)

    with pytest.raises(Exception, match="Fatal actuation failure"):
        asyncio.run(_ome_schedules_the_link(scheduler))

    assert agent.link_up_requests == 1, "the Scheduler commanded the link once"
    assert PAIR not in scheduler._actual_links, "an unproven link is held as actual"
    announced_up = [
        message for subject, message in published.messages if subject == link_up_subject(SESSION)
    ]
    assert announced_up == [], f"an unproven link was announced as up: {announced_up}"
    shown_active = [
        message["active_pairs"]
        for subject, message in published.messages
        if subject.startswith(actual_links_subscribe_subject(SESSION).rstrip(">*"))
        and message["active_pairs"]
    ]
    assert shown_active == [], f"an unproven link was published as kernel-actual: {shown_active}"
    halts = [
        message
        for _subject, message in published.messages
        if message.get("code") == SchedulerOpsCode.ACTUATION_HALTED.value
    ]
    assert len(halts) == 1, "the operator is told once that dispatch halted"
    assert scheduler._dispatch_blocked_reason is not None, "dispatch continues after the halt"
