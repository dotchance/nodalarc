# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Unit tests for the Scheduler dispatch actuator boundary."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

from nodalarc.models.link_events import LinkDecisionProvenance
from nodalarc.proto import node_agent_pb2
from scheduler.desired_state import ActiveLinkInfo
from scheduler.dispatch_actuator import (
    MAX_NODE_AGENT_INTERFACES_PER_COMMAND,
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
