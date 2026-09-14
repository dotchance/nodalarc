# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Unit tests for the Scheduler dispatch actuator boundary."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

import pytest
from nodalarc.models.link_events import LinkDecisionProvenance
from nodalarc.models.scheduler_ops import ActuationFailureClass
from nodalarc.proto import node_agent_pb2
from scheduler.desired_state import ActiveLinkInfo
from scheduler.dispatch_actuator import (
    MAX_NODE_AGENT_INTERFACES_PER_COMMAND,
    _send_batch_down_to_agent,
    _send_batch_up_to_agent,
    _send_chunked_command,
    _send_kernel_inventory_to_agent,
    _send_latency_to_agent,
    send_authoritative_latency_updates,
    send_batch_up,
)
from scheduler.latency_compensator import LatencyCompensation

PAIR = ("sat-a", "sat-b")
SIM_TIME = datetime(2026, 1, 1, tzinfo=UTC)
SESSION_ID = "test-session"
WIRING_GENERATION = "sha256:" + "a" * 64


class _Locator:
    def link_locality(self, _node_a: str, _node_b: str) -> int:
        return node_agent_pb2.LOCALITY_LOCAL

    def agent_addr(self, node_id: str) -> str:
        return f"agent-{node_id}"

    def k3s_node(self, node_id: str) -> str:
        return f"k3s-{node_id}"

    def node_ip(self, _k3s_node: str) -> str | None:
        return None


class _SingleAgentLocator(_Locator):
    def agent_addr(self, _node_id: str) -> str:
        return "agent-one"


class _Stub:
    def __init__(self, fail_node: str | None = None) -> None:
        self.fail_node = fail_node
        self.requests = []

    async def async_batch_link_up(self, req):
        self.requests.append(req)
        results = []
        for iface in req.interfaces:
            success = iface.node_id != self.fail_node
            results.append(
                node_agent_pb2.InterfaceResult(
                    node_id=iface.node_id,
                    interface_name=iface.interface_name,
                    success=success,
                    verified=success,
                    error_message="" if success else "boom",
                )
            )
        return node_agent_pb2.BatchLinkUpResponse(
            success=all(result.success for result in results),
            error_message="",
            interfaces_upped=sum(1 for result in results if result.success),
            apply_time_ms=1.0,
            interface_results=results,
        )

    async def async_set_latency(self, req):
        self.requests.append(req)
        return node_agent_pb2.SetLatencyResponse(
            success=True,
            error_message="",
            entries_updated=len(req.entries),
            entry_results=[
                node_agent_pb2.LatencyResult(
                    node_id=entry.node_id,
                    interface_name=entry.interface_name,
                    success=True,
                    verified=True,
                )
                for entry in req.entries
            ],
        )


class _Pool:
    def __init__(self, fail_node: str | None = None) -> None:
        self.stubs: dict[str, _Stub] = {}
        self.fail_node = fail_node

    def get_stub(self, agent_addr: str) -> _Stub:
        self.stubs.setdefault(agent_addr, _Stub(self.fail_node))
        return self.stubs[agent_addr]


class _Js:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    async def publish(self, subject: str, payload: bytes) -> None:
        self.published.append((subject, json.loads(payload)))


def _desired() -> dict[tuple[str, str], ActiveLinkInfo]:
    return {
        PAIR: ActiveLinkInfo(
            interface_a="isl0",
            interface_b="isl1",
            latency_ms=10.0,
            bandwidth_mbps=1000.0,
            link_type="isl",
            range_km=2997.92458,
            authority_sim_time=SIM_TIME,
            authority_source="snapshot",
            authority_sequence=7,
        )
    }


def _many_desired(pair_count: int) -> dict[tuple[str, str], ActiveLinkInfo]:
    desired: dict[tuple[str, str], ActiveLinkInfo] = {}
    for idx in range(pair_count):
        pair = (f"sat-a-{idx:03d}", f"sat-b-{idx:03d}")
        desired[pair] = ActiveLinkInfo(
            interface_a="isl0",
            interface_b="isl1",
            latency_ms=10.0,
            bandwidth_mbps=1000.0,
            link_type="isl",
            range_km=2997.92458,
            authority_sim_time=SIM_TIME,
            authority_source="snapshot",
            authority_sequence=7,
        )
    return desired


def _compensation(_node_a: str, _node_b: str, orbital_ms: float) -> LatencyCompensation:
    return LatencyCompensation(
        orbital_one_way_ms=orbital_ms,
        substrate_rtt_ms=2.0,
        substrate_one_way_ms=1.0,
        netem_one_way_ms=9.0,
        rtt_to_one_way_policy="half-rtt",
    )


def _validate(_pair, _info, _sim_time, *, operation: str) -> None:
    assert operation in {"LinkUp", "LatencyUpdate"}


def _provenance(info, compensation, sim_time):
    return LinkDecisionProvenance(
        authority_source=info.authority_source,
        authority_sim_time=sim_time,
        authority_sequence=info.authority_sequence,
        authority_age_ms=0.0,
        range_km=info.range_km,
        orbital_one_way_ms=info.latency_ms,
        substrate_rtt_ms=compensation.substrate_rtt_ms,
        substrate_one_way_ms=compensation.substrate_one_way_ms,
        netem_one_way_ms=compensation.netem_one_way_ms,
        rtt_to_one_way_policy=compensation.rtt_to_one_way_policy,
    )


def test_send_batch_up_publishes_link_up_only_after_all_interface_acks():
    pool = _Pool()
    js = _Js()

    result = asyncio.run(
        send_batch_up(
            pairs={PAIR},
            desired=_desired(),
            locator=_Locator(),
            pool=pool,
            js=js,
            subj_link_up="links.up",
            sim_iso=SIM_TIME.isoformat(),
            sim_time=SIM_TIME,
            gs_capacities={},
            latency_compensation=_compensation,
            validate_authority_freshness=_validate,
            link_provenance=_provenance,
            session_id=SESSION_ID,
            wiring_generation=WIRING_GENERATION,
        )
    )

    assert result.succeeded_pairs == {PAIR}
    assert result.failed_pairs == set()
    assert len(js.published) == 1
    subject, event = js.published[0]
    assert subject == "links.up"
    assert event["link_type"] == "isl"
    assert event["provenance"]["netem_one_way_ms"] == 9.0
    req = pool.stubs["agent-sat-a"].requests[0]
    assert req.envelope.session_id == SESSION_ID
    assert req.envelope.wiring_generation == WIRING_GENERATION
    assert req.envelope.operation_kind == "BatchLinkUp"


def test_send_batch_up_requires_every_interface_ack_for_pair_success():
    pool = _Pool(fail_node="sat-b")
    js = _Js()

    result = asyncio.run(
        send_batch_up(
            pairs={PAIR},
            desired=_desired(),
            locator=_Locator(),
            pool=pool,
            js=js,
            subj_link_up="links.up",
            sim_iso=SIM_TIME.isoformat(),
            sim_time=SIM_TIME,
            gs_capacities={},
            latency_compensation=_compensation,
            validate_authority_freshness=_validate,
            link_provenance=_provenance,
            session_id=SESSION_ID,
            wiring_generation=WIRING_GENERATION,
        )
    )

    assert result.succeeded_pairs == set()
    assert result.failed_pairs == {PAIR}
    assert js.published == []


def _actuation_latency_records(caplog) -> list:
    return [r for r in caplog.records if getattr(r, "event", None) == "actuation_latency"]


def test_send_batch_up_logs_successful_wall_clock_actuation_latency_at_debug(caplog):
    # Successful actuator timing is useful for targeted measurement runs, but it is
    # high-volume control-loop telemetry and must not fill operator logs.
    pool = _Pool()
    js = _Js()
    with caplog.at_level(logging.DEBUG, logger="scheduler.dispatch_actuator"):
        result = asyncio.run(
            send_batch_up(
                pairs={PAIR},
                desired=_desired(),
                locator=_Locator(),
                pool=pool,
                js=js,
                subj_link_up="links.up",
                sim_iso=SIM_TIME.isoformat(),
                sim_time=SIM_TIME,
                gs_capacities={},
                latency_compensation=_compensation,
                validate_authority_freshness=_validate,
                link_provenance=_provenance,
                session_id=SESSION_ID,
                wiring_generation=WIRING_GENERATION,
            )
        )

    assert result.succeeded_pairs == {PAIR}
    recs = _actuation_latency_records(caplog)
    assert len(recs) == 1
    rec = recs[0]
    assert rec.operation == "BatchLinkUp"
    assert rec.pair_count == 1
    assert rec.succeeded == 1
    assert rec.failed == 0
    assert isinstance(rec.actuation_latency_ms, float)
    assert rec.actuation_latency_ms >= 0.0
    assert rec.levelno == logging.DEBUG


def test_failed_actuation_logs_latency_at_warning(caplog):
    # A batch with failures logs its latency LOUDLY (WARNING) — a slow/failed
    # actuation must not hide in INFO noise during measurement runs.
    pool = _Pool(fail_node="sat-b")
    js = _Js()
    with caplog.at_level(logging.INFO, logger="scheduler.dispatch_actuator"):
        result = asyncio.run(
            send_batch_up(
                pairs={PAIR},
                desired=_desired(),
                locator=_Locator(),
                pool=pool,
                js=js,
                subj_link_up="links.up",
                sim_iso=SIM_TIME.isoformat(),
                sim_time=SIM_TIME,
                gs_capacities={},
                latency_compensation=_compensation,
                validate_authority_freshness=_validate,
                link_provenance=_provenance,
                session_id=SESSION_ID,
                wiring_generation=WIRING_GENERATION,
            )
        )

    assert result.failed_pairs == {PAIR}
    recs = _actuation_latency_records(caplog)
    assert len(recs) == 1
    assert recs[0].failed == 1
    assert recs[0].levelno == logging.WARNING


def test_no_actuation_latency_log_without_a_dispatch(caplog):
    # No agents to dispatch to -> no RPC round-trip -> no latency record. Guards
    # against logging spurious ~0ms "actuations" on no-op reconciles.
    js = _Js()
    with caplog.at_level(logging.INFO, logger="scheduler.dispatch_actuator"):
        result = asyncio.run(
            send_batch_up(
                pairs=set(),
                desired={},
                locator=_Locator(),
                pool=_Pool(),
                js=js,
                subj_link_up="links.up",
                sim_iso=SIM_TIME.isoformat(),
                sim_time=SIM_TIME,
                gs_capacities={},
                latency_compensation=_compensation,
                validate_authority_freshness=_validate,
                link_provenance=_provenance,
                session_id=SESSION_ID,
                wiring_generation=WIRING_GENERATION,
            )
        )

    assert result.succeeded_pairs == set()
    assert _actuation_latency_records(caplog) == []


def test_send_batch_up_chunks_large_single_agent_batches():
    pair_count = MAX_NODE_AGENT_INTERFACES_PER_COMMAND // 2 + 2
    desired = _many_desired(pair_count)
    pool = _Pool()
    js = _Js()

    result = asyncio.run(
        send_batch_up(
            pairs=set(desired),
            desired=desired,
            locator=_SingleAgentLocator(),
            pool=pool,
            js=js,
            subj_link_up="links.up",
            sim_iso=SIM_TIME.isoformat(),
            sim_time=SIM_TIME,
            gs_capacities={},
            latency_compensation=_compensation,
            validate_authority_freshness=_validate,
            link_provenance=_provenance,
            session_id=SESSION_ID,
            wiring_generation=WIRING_GENERATION,
        )
    )

    requests = pool.stubs["agent-one"].requests
    assert result.succeeded_pairs == set(desired)
    assert result.failed_pairs == set()
    assert len(requests) == 2
    assert all(len(req.interfaces) <= MAX_NODE_AGENT_INTERFACES_PER_COMMAND for req in requests)
    assert requests[0].envelope.operation_id.endswith("-part001of002")
    assert requests[1].envelope.operation_id.endswith("-part002of002")
    assert len(js.published) == pair_count


def test_ground_latency_update_updates_both_local_shaped_interfaces():
    pair = ("gs-den", "sat-a")
    desired = {
        pair: ActiveLinkInfo(
            interface_a="term0",
            interface_b="gnd0",
            latency_ms=10.0,
            bandwidth_mbps=1000.0,
            link_type="ground",
            range_km=2997.92458,
            authority_sim_time=SIM_TIME,
            authority_source="snapshot",
            authority_sequence=7,
        )
    }
    pool = _Pool()
    js = _Js()

    result = asyncio.run(
        send_authoritative_latency_updates(
            pairs={pair},
            desired=desired,
            locator=_Locator(),
            pool=pool,
            js=js,
            subj_latency="links.latency",
            sim_time=SIM_TIME,
            gs_capacities={"gs-den": 1},
            latency_compensation=_compensation,
            validate_authority_freshness=_validate,
            link_provenance=_provenance,
            session_id=SESSION_ID,
            wiring_generation=WIRING_GENERATION,
        )
    )

    assert result.succeeded_pairs == {pair}
    assert result.failed_pairs == set()
    stub = pool.stubs["agent-sat-a"]
    req = stub.requests[0]
    assert req.envelope.session_id == SESSION_ID
    assert req.envelope.wiring_generation == WIRING_GENERATION
    assert req.envelope.operation_kind == "SetLatency"
    assert {(entry.node_id, entry.interface_name) for entry in req.entries} == {
        ("gs-den", "term0"),
        ("sat-a", "gnd0"),
    }
    assert len(js.published) == 1
    assert datetime.fromisoformat(js.published[0][1]["sim_time"].replace("Z", "+00:00")) == SIM_TIME


# --- one command sender: request shape and failure paths per operation ---


def _ok_results(items, result_type):
    return [
        result_type(
            node_id=item.node_id, interface_name=item.interface_name, success=True, verified=True
        )
        for item in items
    ]


class _RecordingStub:
    """Answers every operation with success and records the requests in order."""

    def __init__(self) -> None:
        self.requests: list = []

    async def async_batch_link_down(self, req):
        self.requests.append(req)
        return node_agent_pb2.BatchLinkDownResponse(
            success=True,
            interface_results=_ok_results(req.interfaces, node_agent_pb2.InterfaceResult),
        )

    async def async_batch_link_up(self, req):
        self.requests.append(req)
        return node_agent_pb2.BatchLinkUpResponse(
            success=True,
            interface_results=_ok_results(req.interfaces, node_agent_pb2.InterfaceResult),
        )

    async def async_set_latency(self, req):
        self.requests.append(req)
        return node_agent_pb2.SetLatencyResponse(
            success=True, entry_results=_ok_results(req.entries, node_agent_pb2.LatencyResult)
        )

    async def async_kernel_inventory(self, req):
        self.requests.append(req)
        return node_agent_pb2.KernelInventoryResponse(
            success=True,
            entry_results=_ok_results(req.entries, node_agent_pb2.KernelInventoryEntryResult),
        )


class _OneStubPool:
    def __init__(self, stub) -> None:
        self.stub = stub
        self.lookups: list[str] = []

    def get_stub(self, agent_addr: str):
        self.lookups.append(agent_addr)
        return self.stub


def _items(kind, count: int):
    return [kind(node_id=f"n{i:03d}", interface_name="isl0") for i in range(count)]


def _senders(pool, count: int):
    sim_iso = SIM_TIME.isoformat()
    common = {"pool": pool, "session_id": SESSION_ID, "wiring_generation": WIRING_GENERATION}
    return {
        "BatchLinkDown": (
            lambda: _send_batch_down_to_agent(
                addr="agent-x",
                interfaces=_items(node_agent_pb2.InterfaceDown, count),
                sim_iso=sim_iso,
                **common,
            ),
            node_agent_pb2.BatchLinkDownRequest,
            f"{sim_iso}-down-agent-x",
            "interfaces",
        ),
        "BatchLinkUp": (
            lambda: _send_batch_up_to_agent(
                addr="agent-x",
                interfaces=_items(node_agent_pb2.InterfaceUp, count),
                sim_iso=sim_iso,
                **common,
            ),
            node_agent_pb2.BatchLinkUpRequest,
            f"{sim_iso}-up-agent-x",
            "interfaces",
        ),
        "SetLatency": (
            lambda: _send_latency_to_agent(
                agent_addr="agent-x",
                entries=_items(node_agent_pb2.LatencyEntry, count),
                sim_time=SIM_TIME,
                **common,
            ),
            node_agent_pb2.SetLatencyRequest,
            f"{sim_iso}-latency-agent-x",
            "entries",
        ),
        "KernelInventory": (
            lambda: _send_kernel_inventory_to_agent(
                addr="agent-x",
                entries=_items(node_agent_pb2.KernelInventoryEntry, count),
                sim_iso=sim_iso,
                gs_id="gs-den",
                **common,
            ),
            node_agent_pb2.KernelInventoryRequest,
            f"{sim_iso}-kernel-inventory-gs-den-agent-x",
            "entries",
        ),
    }


def test_every_sender_preserves_its_request_shape_across_chunks():
    count = MAX_NODE_AGENT_INTERFACES_PER_COMMAND + 1
    for operation in ("BatchLinkDown", "BatchLinkUp", "SetLatency", "KernelInventory"):
        stub = _RecordingStub()
        pool = _OneStubPool(stub)
        start, request_type, base, item_field = _senders(pool, count)[operation]
        result = asyncio.run(start())
        assert pool.lookups == ["agent-x"]
        assert [type(req) for req in stub.requests] == [request_type, request_type]
        assert [req.envelope.operation_id for req in stub.requests] == [
            f"{base}-part001of002",
            f"{base}-part002of002",
        ]
        assert {req.envelope.operation_kind for req in stub.requests} == {operation}
        assert {req.envelope.session_id for req in stub.requests} == {SESSION_ID}
        assert {req.envelope.wiring_generation for req in stub.requests} == {WIRING_GENERATION}
        assert [len(getattr(req, item_field)) for req in stub.requests] == [
            MAX_NODE_AGENT_INTERFACES_PER_COMMAND,
            1,
        ]
        field_names = {field.name for field in request_type.DESCRIPTOR.fields}
        if operation == "SetLatency":
            assert "target_sim_time" not in field_names
        else:
            assert {req.target_sim_time for req in stub.requests} == {SIM_TIME.isoformat()}
        if operation == "KernelInventory":
            assert {req.gs_id for req in stub.requests} == {"gs-den"}
        assert result.failure_class == ActuationFailureClass.NONE
        assert len(result.success_acks) == count
        assert len(result.details["chunks"]) == 2


def test_single_chunk_result_passes_through_and_empty_input_sends_nothing():
    for operation in ("BatchLinkDown", "BatchLinkUp", "SetLatency", "KernelInventory"):
        stub = _RecordingStub()
        pool = _OneStubPool(stub)
        start, _request_type, base, _field = _senders(pool, 3)[operation]
        result = asyncio.run(start())
        assert [req.envelope.operation_id for req in stub.requests] == [base]
        assert "chunks" not in result.details
        assert len(result.details["interface_results"]) == 3
        assert result.details["requested"] == result.details["returned"]

        stub = _RecordingStub()
        pool = _OneStubPool(stub)
        start, _request_type, _base, _field = _senders(pool, 0)[operation]
        result = asyncio.run(start())
        assert stub.requests == []
        assert result.requested == ()
        assert result.failure_class == ActuationFailureClass.NONE
        assert result.details == {"agent_addr": "agent-x", "operation": operation, "chunks": []}


class _MixedStub(_RecordingStub):
    """First chunk: one interface fails cleanly. Second chunk: transport error."""

    async def async_batch_link_up(self, req):
        self.requests.append(req)
        if len(self.requests) == 2:
            raise RuntimeError("transport lost")
        results = []
        for index, iface in enumerate(req.interfaces):
            ok = index != 0
            results.append(
                node_agent_pb2.InterfaceResult(
                    node_id=iface.node_id,
                    interface_name=iface.interface_name,
                    success=ok,
                    verified=ok,
                    error_message="" if ok else "boom",
                )
            )
        return node_agent_pb2.BatchLinkUpResponse(success=False, interface_results=results)


def test_sender_continues_after_a_failed_chunk_and_merges_every_chunks_evidence():
    stub = _MixedStub()
    pool = _OneStubPool(stub)
    count = MAX_NODE_AGENT_INTERFACES_PER_COMMAND + 2
    result = asyncio.run(
        _send_batch_up_to_agent(
            addr="agent-x",
            interfaces=_items(node_agent_pb2.InterfaceUp, count),
            pool=pool,
            sim_iso=SIM_TIME.isoformat(),
            session_id=SESSION_ID,
            wiring_generation=WIRING_GENERATION,
        )
    )
    assert len(stub.requests) == 2, (
        "the second chunk is sent after the first is classified as failed"
    )
    first_acks = {
        ("agent-x", iface.node_id, iface.interface_name) for iface in stub.requests[0].interfaces
    } - {("agent-x", "n000", "isl0")}
    assert result.success_acks == frozenset(first_acks)
    assert result.failure_class == ActuationFailureClass.AGENT_UNREACHABLE
    assert result.dirty_kernel is True
    assert result.unknown_outcome is True
    assert result.fence_failure is False
    assert len(result.requested) == count
    chunks = result.details["chunks"]
    assert [chunk["error_code"] for chunk in chunks] == [
        "NODE_AGENT_ERROR_UNSPECIFIED",
        "TRANSPORT",
    ]
    assert chunks[0]["interface_results"][0]["error_message"] == "boom"
    assert chunks[1]["error_message"] == "transport lost"
    assert chunks[1]["requested"] == [["n064", "isl0"], ["n065", "isl0"]]
    assert stub.requests[0].interfaces[0].node_id == "n000"
    assert result.details["error_message"] == "transport lost"
    assert len(result.details["interface_results"]) == MAX_NODE_AGENT_INTERFACES_PER_COMMAND


class _CancellingStub(_RecordingStub):
    async def async_batch_link_up(self, req):
        raise asyncio.CancelledError()


def test_cancellation_and_construction_errors_propagate_unclassified():
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            _send_batch_up_to_agent(
                addr="agent-x",
                interfaces=_items(node_agent_pb2.InterfaceUp, 1),
                pool=_OneStubPool(_CancellingStub()),
                sim_iso=SIM_TIME.isoformat(),
                session_id=SESSION_ID,
                wiring_generation=WIRING_GENERATION,
            )
        )

    stub = _RecordingStub()

    def _broken_request(_envelope, _chunk):
        raise ValueError("cannot build")

    with pytest.raises(ValueError, match="cannot build"):
        asyncio.run(
            _send_chunked_command(
                addr="agent-x",
                items=_items(node_agent_pb2.InterfaceUp, 1),
                pool=_OneStubPool(stub),
                operation="BatchLinkUp",
                operation_id_base="base",
                session_id=SESSION_ID,
                wiring_generation=WIRING_GENERATION,
                build_request=_broken_request,
                send=lambda s, req: s.async_batch_link_up(req),
            )
        )
    assert stub.requests == []

    class _NoStubPool:
        def get_stub(self, _addr):
            raise LookupError("no agent")

    with pytest.raises(LookupError, match="no agent"):
        asyncio.run(
            _send_batch_up_to_agent(
                addr="agent-x",
                interfaces=_items(node_agent_pb2.InterfaceUp, 1),
                pool=_NoStubPool(),
                sim_iso=SIM_TIME.isoformat(),
                session_id=SESSION_ID,
                wiring_generation=WIRING_GENERATION,
            )
        )


class _HandshakeStub(_Stub):
    """agent-n1's request waits until agent-n2's request has arrived."""

    def __init__(self, name: str, arrived: asyncio.Event) -> None:
        super().__init__()
        self.name = name
        self.arrived = arrived

    async def async_batch_link_up(self, req):
        if self.name == "agent-n1":
            await asyncio.wait_for(self.arrived.wait(), timeout=2)
        else:
            self.arrived.set()
        return await super().async_batch_link_up(req)


def test_agents_are_sent_concurrently_while_each_agents_chunks_stay_in_order():
    async def _run():
        arrived = asyncio.Event()
        stubs = {name: _HandshakeStub(name, arrived) for name in ("agent-n1", "agent-n2")}

        class _HandshakePool:
            def get_stub(self, agent_addr: str):
                return stubs[agent_addr]

        pool = _HandshakePool()
        desired = {("n1", "n2"): _desired()[next(iter(_desired()))]}
        return await send_batch_up(
            pairs=set(desired),
            desired=desired,
            locator=_Locator(),
            pool=pool,
            js=_Js(),
            subj_link_up="links.up",
            sim_iso=SIM_TIME.isoformat(),
            sim_time=SIM_TIME,
            gs_capacities={},
            latency_compensation=_compensation,
            validate_authority_freshness=_validate,
            link_provenance=_provenance,
            session_id=SESSION_ID,
            wiring_generation=WIRING_GENERATION,
        ), stubs

    result, stubs = asyncio.run(_run())
    assert result.failed_pairs == set()
    assert len(stubs["agent-n1"].requests) == 1 and len(stubs["agent-n2"].requests) == 1


# --- one ground-endpoint decision: the builders preserve every field on both orderings ---


class _CrossLocator(_Locator):
    def __init__(self, node_ips: dict[str, str]) -> None:
        self._node_ips = node_ips

    def link_locality(self, _node_a: str, _node_b: str) -> int:
        return node_agent_pb2.LOCALITY_CROSS_NODE

    def node_ip(self, k3s_node: str) -> str | None:
        return self._node_ips.get(k3s_node)


GS, SAT = "gs-den", "sat-a"
CAPACITIES = {GS: 1}
NODE_IPS = {"k3s-gs-den": "10.0.0.1", "k3s-sat-a": "10.0.0.2"}


def _ground_info(pair: tuple[str, str]) -> ActiveLinkInfo:
    # interface_a belongs to pair[0]: the station carries term0, the satellite gnd0.
    station_first = pair[0] == GS
    return ActiveLinkInfo(
        interface_a="term0" if station_first else "gnd0",
        interface_b="gnd0" if station_first else "term0",
        latency_ms=10.0,
        bandwidth_mbps=1000.0,
        link_type="ground",
        range_km=2997.92458,
        authority_sim_time=SIM_TIME,
        authority_source="snapshot",
        authority_sequence=7,
    )


def _fields(msg, names):
    return {name: getattr(msg, name) for name in names}


_UP_FIELDS = (
    "node_id",
    "interface_name",
    "peer_node_id",
    "peer_interface_name",
    "link_type",
    "gs_id",
    "sat_id",
    "locality",
    "remote_node_ip",
    "vni",
    "latency_ms",
    "bandwidth_mbps",
)
_DOWN_FIELDS = (
    "node_id",
    "interface_name",
    "peer_node_id",
    "peer_interface_name",
    "link_type",
    "gs_id",
    "sat_id",
    "locality",
    "remote_node_ip",
    "vni",
)
_INVENTORY_FIELDS = _DOWN_FIELDS + ("latency_ms", "bandwidth_mbps", "expected_admin_up")


def _expected_ground_messages(*, locality: int, cross_vni: int, up: bool):
    """The exact per-agent messages for the gs-den <-> sat-a ground link, as the
    unmigrated builders produced them: one message to the satellite's agent for a
    local link, one to each agent for a cross-host link."""
    common = {
        "link_type": node_agent_pb2.LINK_TYPE_GROUND,
        "gs_id": GS,
        "sat_id": SAT,
        "locality": locality,
    }
    extra = {"latency_ms": 9.0, "bandwidth_mbps": 1000.0} if up else {}
    if locality == node_agent_pb2.LOCALITY_LOCAL:
        return {
            "agent-sat-a": [
                {
                    **common,
                    **extra,
                    "node_id": GS,
                    "interface_name": "term0",
                    "peer_node_id": SAT,
                    "peer_interface_name": "gnd0",
                    "remote_node_ip": "",
                    "vni": 0,
                },
            ]
        }
    return {
        "agent-sat-a": [
            {
                **common,
                **extra,
                "node_id": SAT,
                "interface_name": "gnd0",
                "peer_node_id": GS,
                "peer_interface_name": "term0",
                "remote_node_ip": "10.0.0.1",
                "vni": cross_vni,
            },
        ],
        "agent-gs-den": [
            {
                **common,
                **extra,
                "node_id": GS,
                "interface_name": "term0",
                "peer_node_id": SAT,
                "peer_interface_name": "gnd0",
                "remote_node_ip": "10.0.0.2",
                "vni": cross_vni,
            },
        ],
    }


@pytest.mark.parametrize("pair", [(GS, SAT), (SAT, GS)], ids=["station-first", "satellite-first"])
@pytest.mark.parametrize(
    "locality",
    [node_agent_pb2.LOCALITY_LOCAL, node_agent_pb2.LOCALITY_CROSS_NODE],
    ids=["local", "cross-host"],
)
def test_ground_batch_plans_preserve_endpoints_interfaces_and_fields(pair, locality):
    from nodalarc.vxlan import compute_vni
    from scheduler.node_agent_batches import build_link_down_batch_plan, build_link_up_batch_plan

    locator = _Locator() if locality == node_agent_pb2.LOCALITY_LOCAL else _CrossLocator(NODE_IPS)
    cross_vni = compute_vni(GS, SAT, "term0", "gnd0")
    info = _ground_info(pair)

    up = build_link_up_batch_plan(
        pairs={pair},
        desired={pair: info},
        locator=locator,
        gs_capacities=CAPACITIES,
        compensation_for_pair=_compensation,
    )
    assert {
        agent: [_fields(m, _UP_FIELDS) for m in msgs] for agent, msgs in up.agent_ifaces.items()
    } == (_expected_ground_messages(locality=locality, cross_vni=cross_vni, up=True))
    down = build_link_down_batch_plan(
        pairs={pair}, actual_links={pair: info}, locator=locator, gs_capacities=CAPACITIES
    )
    assert {
        agent: [_fields(m, _DOWN_FIELDS) for m in msgs] for agent, msgs in down.agent_ifaces.items()
    } == (_expected_ground_messages(locality=locality, cross_vni=cross_vni, up=False))
    expected_acks = {
        (agent, m["node_id"], m["interface_name"])
        for agent, msgs in _expected_ground_messages(
            locality=locality, cross_vni=cross_vni, up=False
        ).items()
        for m in msgs
    }
    assert up.pair_agent_ifaces[pair] == expected_acks
    assert down.pair_agent_ifaces[pair] == expected_acks


@pytest.mark.parametrize("pair", [(GS, SAT), (SAT, GS)], ids=["station-first", "satellite-first"])
@pytest.mark.parametrize(
    "locality",
    [node_agent_pb2.LOCALITY_LOCAL, node_agent_pb2.LOCALITY_CROSS_NODE],
    ids=["local", "cross-host"],
)
def test_ground_inventory_entries_preserve_endpoints_interfaces_and_fields(pair, locality):
    from nodalarc.vxlan import compute_vni
    from scheduler.dispatch_actuator import _ground_inventory_entries_for_pair

    locator = _Locator() if locality == node_agent_pb2.LOCALITY_LOCAL else _CrossLocator(NODE_IPS)
    info = _ground_info(pair)
    info.netem_one_way_ms = 9.0

    entries, acks = _ground_inventory_entries_for_pair(
        pair=pair, info=info, expected_admin_up=True, locator=locator, gs_capacities=CAPACITIES
    )

    expected = _expected_ground_messages(
        locality=locality, cross_vni=compute_vni(GS, SAT, "term0", "gnd0"), up=True
    )
    expected = {
        agent: [{**m, "expected_admin_up": True} for m in msgs] for agent, msgs in expected.items()
    }
    assert {
        agent: [_fields(e, _INVENTORY_FIELDS) for e in es] for agent, es in entries.items()
    } == expected
    assert acks == {
        (agent, m["node_id"], m["interface_name"]) for agent, msgs in expected.items() for m in msgs
    }


@pytest.mark.parametrize("pair", [(GS, SAT), (SAT, GS)], ids=["station-first", "satellite-first"])
@pytest.mark.parametrize(
    "locality",
    [node_agent_pb2.LOCALITY_LOCAL, node_agent_pb2.LOCALITY_CROSS_NODE],
    ids=["local", "cross-host"],
)
def test_ground_latency_update_preserves_per_side_entries(pair, locality):
    locator = _Locator() if locality == node_agent_pb2.LOCALITY_LOCAL else _CrossLocator(NODE_IPS)
    pool = _Pool()

    result = asyncio.run(
        send_authoritative_latency_updates(
            pairs={pair},
            desired={pair: _ground_info(pair)},
            locator=locator,
            pool=pool,
            js=_Js(),
            subj_latency="links.latency",
            sim_time=SIM_TIME,
            gs_capacities=CAPACITIES,
            latency_compensation=_compensation,
            validate_authority_freshness=_validate,
            link_provenance=_provenance,
            session_id=SESSION_ID,
            wiring_generation=WIRING_GENERATION,
        )
    )

    assert result.succeeded_pairs == {pair}
    observed = {
        agent: sorted(
            (e.node_id, e.interface_name, e.latency_ms, e.gs_id, e.sat_id)
            for req in stub.requests
            for e in req.entries
        )
        for agent, stub in pool.stubs.items()
    }
    if locality == node_agent_pb2.LOCALITY_LOCAL:
        assert observed == {
            "agent-sat-a": [(GS, "term0", 9.0, GS, SAT), (SAT, "gnd0", 9.0, GS, SAT)]
        }
    else:
        assert observed == {
            "agent-sat-a": [(SAT, "gnd0", 9.0, GS, SAT)],
            "agent-gs-den": [(GS, "term0", 9.0, GS, SAT)],
        }


def test_ground_side_and_its_accessors_agree_on_every_pair_shape():
    from scheduler.dispatch_planner import (
        GroundEndpoints,
        ground_endpoints,
        ground_side,
        gs_id_for_pair,
        sat_id_for_gs_pair,
    )

    info = _ground_info((GS, SAT))
    assert ground_side((GS, SAT), CAPACITIES) == 0 and ground_side((SAT, GS), CAPACITIES) == 1
    assert (
        gs_id_for_pair((SAT, GS), CAPACITIES) == GS
        and sat_id_for_gs_pair((SAT, GS), CAPACITIES) == SAT
    )
    assert ground_endpoints((GS, SAT), info, CAPACITIES) == GroundEndpoints(
        GS, SAT, "term0", "gnd0"
    )
    assert ground_endpoints((SAT, GS), _ground_info((SAT, GS)), CAPACITIES) == GroundEndpoints(
        GS, SAT, "term0", "gnd0"
    )
    # Two stations: the first endpoint is the station, the rule every accessor already applied.
    two = {"gs-den": 1, "gs-sfo": 1}
    assert ground_side(("gs-den", "gs-sfo"), two) == 0
    assert (
        gs_id_for_pair(("gs-den", "gs-sfo"), two) == "gs-den"
        and sat_id_for_gs_pair(("gs-den", "gs-sfo"), two) == "gs-sfo"
    )
    assert ground_endpoints(("gs-den", "gs-sfo"), info, two) == GroundEndpoints(
        "gs-den", "gs-sfo", "term0", "gnd0"
    )
    # No station: every accessor answers None.
    assert ground_side((SAT, "sat-b"), CAPACITIES) is None
    assert (
        gs_id_for_pair((SAT, "sat-b"), CAPACITIES) is None
        and sat_id_for_gs_pair((SAT, "sat-b"), CAPACITIES) is None
    )
    assert ground_endpoints((SAT, "sat-b"), info, CAPACITIES) is None


def test_a_ground_link_without_a_station_is_refused_by_every_builder():
    """The declared behavior change: a ground-typed link whose pair has no station
    no longer silently takes node_b as the station."""
    from scheduler.dispatch_actuator import _ground_inventory_entries_for_pair
    from scheduler.node_agent_batches import build_link_down_batch_plan, build_link_up_batch_plan

    pair = (SAT, "sat-b")
    info = _ground_info((GS, SAT))
    with pytest.raises(RuntimeError, match="has no ground station endpoint"):
        build_link_up_batch_plan(
            pairs={pair},
            desired={pair: info},
            locator=_Locator(),
            gs_capacities=CAPACITIES,
            compensation_for_pair=_compensation,
        )
    with pytest.raises(RuntimeError, match="has no ground station endpoint"):
        build_link_down_batch_plan(
            pairs={pair}, actual_links={pair: info}, locator=_Locator(), gs_capacities=CAPACITIES
        )
    with pytest.raises(RuntimeError, match="has no ground station endpoint"):
        _ground_inventory_entries_for_pair(
            pair=pair,
            info=info,
            expected_admin_up=True,
            locator=_Locator(),
            gs_capacities=CAPACITIES,
        )
    with pytest.raises(RuntimeError, match="has no ground station endpoint"):
        asyncio.run(
            send_authoritative_latency_updates(
                pairs={pair},
                desired={pair: info},
                locator=_Locator(),
                pool=_Pool(),
                js=_Js(),
                subj_latency="links.latency",
                sim_time=SIM_TIME,
                gs_capacities=CAPACITIES,
                latency_compensation=_compensation,
                validate_authority_freshness=_validate,
                link_provenance=_provenance,
                session_id=SESSION_ID,
                wiring_generation=WIRING_GENERATION,
            )
        )
