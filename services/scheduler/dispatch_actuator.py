# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Scheduler Node Agent dispatch actuator.

The Dispatcher owns event ordering and state mutation. This module owns the
side-effectful boundary to Node Agent BatchLinkUp/Down, SetLatency, and
read-only KernelInventory verification. Phase 5 keeps proof details instead of
collapsing Node Agent responses to a bare success set.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from nodalarc.models.link_events import LatencyUpdate, LinkDown, LinkUp
from nodalarc.proto import node_agent_pb2
from nodalarc.vxlan import compute_vni

from scheduler.actuation import (
    ActuationResult,
    AgentCommandResult,
    InterfaceAck,
    build_actuation_result,
    classify_agent_exception,
    classify_agent_response,
)
from scheduler.desired_state import ActiveLinkInfo
from scheduler.latency_compensator import LatencyCompensation
from scheduler.node_agent_batches import build_link_down_batch_plan, build_link_up_batch_plan

log = logging.getLogger(__name__)

LinkPair = tuple[str, str]
LatencyCompensationFn = Callable[[str, str, float], LatencyCompensation]
AuthorityFreshnessValidator = Callable[..., None]
LinkProvenanceBuilder = Callable[..., object]

MAX_NODE_AGENT_INTERFACES_PER_COMMAND = 64


def _chunks(items: list, size: int = MAX_NODE_AGENT_INTERFACES_PER_COMMAND):
    for start in range(0, len(items), size):
        yield start // size, items[start : start + size]


def _chunked_operation_id(base: str, chunk_index: int, chunk_count: int) -> str:
    if chunk_count == 1:
        return base
    return f"{base}-part{chunk_index + 1:03d}of{chunk_count:03d}"


def _gs_id_for_pair(pair: LinkPair, gs_capacities: Mapping[str, int], link_type: str) -> str | None:
    if link_type != "ground":
        return None
    return pair[0] if pair[0] in gs_capacities else pair[1]


async def _send_batch_down_to_agent(
    *,
    addr: str,
    interfaces: list[node_agent_pb2.InterfaceDown],
    pool: Any,
    sim_iso: str,
    session_id: str,
    wiring_generation: str,
) -> AgentCommandResult:
    operation = "BatchLinkDown"
    chunks = list(_chunks(interfaces))
    chunk_results: list[AgentCommandResult] = []
    stub = pool.get_stub(addr)
    for chunk_index, chunk in chunks:
        req = node_agent_pb2.BatchLinkDownRequest(
            envelope=node_agent_pb2.CommandEnvelope(
                operation_id=_chunked_operation_id(
                    f"{sim_iso}-down-{addr}", chunk_index, len(chunks)
                ),
                session_id=session_id,
                wiring_generation=wiring_generation,
                operation_kind=operation,
            ),
            target_sim_time=sim_iso,
            interfaces=chunk,
        )
        try:
            result = await stub.async_batch_link_down(req)
            classified = classify_agent_response(
                result=result,
                requested_interfaces=chunk,
                agent_addr=addr,
                operation=operation,
            )
        except Exception as exc:
            classified = classify_agent_exception(
                exc=exc,
                requested_interfaces=chunk,
                agent_addr=addr,
                operation=operation,
            )
        chunk_results.append(classified)
    return _merge_agent_results(addr=addr, operation=operation, results=chunk_results)


async def _send_batch_up_to_agent(
    *,
    addr: str,
    interfaces: list[node_agent_pb2.InterfaceUp],
    pool: Any,
    sim_iso: str,
    session_id: str,
    wiring_generation: str,
) -> AgentCommandResult:
    operation = "BatchLinkUp"
    chunks = list(_chunks(interfaces))
    chunk_results: list[AgentCommandResult] = []
    stub = pool.get_stub(addr)
    for chunk_index, chunk in chunks:
        req = node_agent_pb2.BatchLinkUpRequest(
            envelope=node_agent_pb2.CommandEnvelope(
                operation_id=_chunked_operation_id(
                    f"{sim_iso}-up-{addr}", chunk_index, len(chunks)
                ),
                session_id=session_id,
                wiring_generation=wiring_generation,
                operation_kind=operation,
            ),
            target_sim_time=sim_iso,
            interfaces=chunk,
        )
        try:
            result = await stub.async_batch_link_up(req)
            classified = classify_agent_response(
                result=result,
                requested_interfaces=chunk,
                agent_addr=addr,
                operation=operation,
            )
        except Exception as exc:
            classified = classify_agent_exception(
                exc=exc,
                requested_interfaces=chunk,
                agent_addr=addr,
                operation=operation,
            )
        chunk_results.append(classified)
    return _merge_agent_results(addr=addr, operation=operation, results=chunk_results)


async def _send_latency_to_agent(
    *,
    agent_addr: str,
    entries: list[node_agent_pb2.LatencyEntry],
    pool: Any,
    sim_time: datetime,
    session_id: str,
    wiring_generation: str,
) -> AgentCommandResult:
    operation = "SetLatency"
    chunks = list(_chunks(entries))
    chunk_results: list[AgentCommandResult] = []
    stub = pool.get_stub(agent_addr)
    for chunk_index, chunk in chunks:
        req = node_agent_pb2.SetLatencyRequest(
            envelope=node_agent_pb2.CommandEnvelope(
                operation_id=_chunked_operation_id(
                    f"{sim_time.isoformat()}-latency-{agent_addr}", chunk_index, len(chunks)
                ),
                session_id=session_id,
                wiring_generation=wiring_generation,
                operation_kind=operation,
            ),
            entries=chunk,
        )
        try:
            result = await stub.async_set_latency(req)
            classified = classify_agent_response(
                result=result,
                requested_interfaces=chunk,
                agent_addr=agent_addr,
                operation=operation,
            )
        except Exception as exc:
            classified = classify_agent_exception(
                exc=exc,
                requested_interfaces=chunk,
                agent_addr=agent_addr,
                operation=operation,
            )
        chunk_results.append(classified)
    return _merge_agent_results(addr=agent_addr, operation=operation, results=chunk_results)


def _merge_agent_results(
    *, addr: str, operation: str, results: list[AgentCommandResult]
) -> AgentCommandResult:
    from scheduler.actuation import ActuationFailureClass

    if not results:
        return AgentCommandResult(
            agent_addr=addr,
            operation=operation,
            requested=(),
            success_acks=frozenset(),
            failure_class=ActuationFailureClass.NONE,
            dirty_kernel=False,
            unknown_outcome=False,
            fence_failure=False,
            details={"agent_addr": addr, "operation": operation, "chunks": []},
        )

    precedence = [
        ActuationFailureClass.FENCE,
        ActuationFailureClass.GROUND_KERNEL_DIRTY,
        ActuationFailureClass.GROUND_UNKNOWN,
        ActuationFailureClass.GROUND_CLEAN_FAILURE,
    ]
    failure_class = ActuationFailureClass.NONE
    for candidate in precedence:
        if any(result.failure_class == candidate for result in results):
            failure_class = candidate
            break
    return AgentCommandResult(
        agent_addr=addr,
        operation=operation,
        requested=tuple(item for result in results for item in result.requested),
        success_acks=frozenset().union(*(result.success_acks for result in results)),
        failure_class=failure_class,
        dirty_kernel=any(result.dirty_kernel for result in results),
        unknown_outcome=any(result.unknown_outcome for result in results),
        fence_failure=any(result.fence_failure for result in results),
        details={
            "agent_addr": addr,
            "operation": operation,
            "dirty_kernel": any(result.dirty_kernel for result in results),
            "unknown_outcome": any(result.unknown_outcome for result in results),
            "fence_failure": any(result.fence_failure for result in results),
            "chunks": [result.details for result in results],
        },
    )


def _ground_inventory_entries_for_pair(
    *,
    pair: LinkPair,
    info: ActiveLinkInfo,
    expected_admin_up: bool,
    locator: Any,
    gs_capacities: Mapping[str, int],
    latency_compensation: LatencyCompensationFn,
) -> tuple[dict[str, list[node_agent_pb2.KernelInventoryEntry]], set[InterfaceAck]]:
    if info.link_type != "ground":
        raise ValueError(f"KernelInventory is ground-only; got {pair} type={info.link_type!r}")
    node_a, node_b = pair
    gs_id = node_a if node_a in gs_capacities else node_b
    sat_id = node_b if node_a in gs_capacities else node_a
    gs_iface = info.interface_a if node_a in gs_capacities else info.interface_b
    sat_iface = info.interface_b if node_a in gs_capacities else info.interface_a
    locality = locator.link_locality(node_a, node_b)
    if locality is None:
        raise RuntimeError(
            f"Cannot verify KernelInventory for {node_a}<->{node_b}: pod placement is unknown"
        )
    vni = (
        compute_vni(gs_id, sat_id, gs_iface, sat_iface)
        if locality == node_agent_pb2.LOCALITY_CROSS_NODE
        else 0
    )
    latency_ms = 0.0
    bandwidth_mbps = 0.0
    if expected_admin_up:
        latency_ms = latency_compensation(node_a, node_b, info.latency_ms).netem_one_way_ms
        bandwidth_mbps = info.bandwidth_mbps

    entries_by_agent: dict[str, list[node_agent_pb2.KernelInventoryEntry]] = {}
    ack_keys: set[InterfaceAck] = set()

    def _add(agent: str, node_id: str, iface: str, peer_node: str, peer_iface: str) -> None:
        entry = node_agent_pb2.KernelInventoryEntry(
            node_id=node_id,
            interface_name=iface,
            link_type=node_agent_pb2.LINK_TYPE_GROUND,
            gs_id=gs_id,
            sat_id=sat_id,
            peer_node_id=peer_node,
            peer_interface_name=peer_iface,
            locality=locality,
            vni=vni,
            latency_ms=latency_ms,
            bandwidth_mbps=bandwidth_mbps,
            expected_admin_up=expected_admin_up,
        )
        entries_by_agent.setdefault(agent, []).append(entry)
        ack_keys.add((agent, node_id, iface))

    if locality == node_agent_pb2.LOCALITY_LOCAL:
        _add(locator.agent_addr(sat_id), gs_id, gs_iface, sat_id, sat_iface)
    else:
        _add(locator.agent_addr(sat_id), sat_id, sat_iface, gs_id, gs_iface)
        _add(locator.agent_addr(gs_id), gs_id, gs_iface, sat_id, sat_iface)
    return entries_by_agent, ack_keys


async def _send_kernel_inventory_to_agent(
    *,
    addr: str,
    entries: list[node_agent_pb2.KernelInventoryEntry],
    pool: Any,
    sim_iso: str,
    session_id: str,
    wiring_generation: str,
    gs_id: str,
) -> AgentCommandResult:
    operation = "KernelInventory"
    chunks = list(_chunks(entries))
    chunk_results: list[AgentCommandResult] = []
    stub = pool.get_stub(addr)
    for chunk_index, chunk in chunks:
        req = node_agent_pb2.KernelInventoryRequest(
            envelope=node_agent_pb2.CommandEnvelope(
                operation_id=_chunked_operation_id(
                    f"{sim_iso}-kernel-inventory-{gs_id}-{addr}", chunk_index, len(chunks)
                ),
                session_id=session_id,
                wiring_generation=wiring_generation,
                operation_kind=operation,
            ),
            target_sim_time=sim_iso,
            gs_id=gs_id,
            entries=chunk,
        )
        try:
            result = await stub.async_kernel_inventory(req)
            classified = classify_agent_response(
                result=result,
                requested_interfaces=chunk,
                agent_addr=addr,
                operation=operation,
            )
        except Exception as exc:
            classified = classify_agent_exception(
                exc=exc,
                requested_interfaces=chunk,
                agent_addr=addr,
                operation=operation,
            )
        chunk_results.append(classified)
    return _merge_agent_results(addr=addr, operation=operation, results=chunk_results)


async def verify_ground_kernel_inventory(
    *,
    gs_id: str,
    expected_up: Mapping[LinkPair, ActiveLinkInfo],
    expected_down: Mapping[LinkPair, ActiveLinkInfo],
    locator: Any,
    pool: Any,
    sim_iso: str,
    sim_time: datetime,
    gs_capacities: Mapping[str, int],
    latency_compensation: LatencyCompensationFn,
    session_id: str,
    wiring_generation: str,
) -> ActuationResult:
    """Read-only GS-facing kernel proof for the Scheduler actuation lifecycle."""
    del sim_time  # carried by the public signature for call-site symmetry and future audit fields
    entries_by_agent: dict[str, list[node_agent_pb2.KernelInventoryEntry]] = {}
    pair_agent_ifaces: dict[LinkPair, set[InterfaceAck]] = {}
    pair_link_type: dict[LinkPair, str] = {}
    pair_gs_id: dict[LinkPair, str | None] = {}

    for expected_admin_up, pairs in ((True, expected_up), (False, expected_down)):
        for pair, info in pairs.items():
            if _gs_id_for_pair(pair, gs_capacities, info.link_type) != gs_id:
                continue
            pair_link_type[pair] = "ground"
            pair_gs_id[pair] = gs_id
            agent_entries, ack_keys = _ground_inventory_entries_for_pair(
                pair=pair,
                info=info,
                expected_admin_up=expected_admin_up,
                locator=locator,
                gs_capacities=gs_capacities,
                latency_compensation=latency_compensation,
            )
            pair_agent_ifaces.setdefault(pair, set()).update(ack_keys)
            for agent, entries in agent_entries.items():
                entries_by_agent.setdefault(agent, []).extend(entries)

    agent_results: list[AgentCommandResult] = []
    if entries_by_agent:
        agent_results = list(
            await asyncio.gather(
                *[
                    _send_kernel_inventory_to_agent(
                        addr=agent_addr,
                        entries=entries_by_agent[agent_addr],
                        pool=pool,
                        sim_iso=sim_iso,
                        session_id=session_id,
                        wiring_generation=wiring_generation,
                        gs_id=gs_id,
                    )
                    for agent_addr in entries_by_agent
                ]
            )
        )

    requested = sorted(set(expected_up) | set(expected_down))
    return build_actuation_result(
        operation="KernelInventory",
        requested_pairs=requested,
        pair_agent_ifaces=pair_agent_ifaces,
        pair_link_type=pair_link_type,
        pair_gs_id=pair_gs_id,
        agent_results=agent_results,
    )


async def send_batch_down(
    *,
    pairs: set[LinkPair],
    actual_links: Mapping[LinkPair, ActiveLinkInfo],
    locator: Any,
    pool: Any,
    js: Any,
    subj_link_down: str,
    sim_iso: str,
    sim_time: datetime,
    down_reasons: Mapping[LinkPair, str],
    gs_capacities: Mapping[str, int],
    session_id: str,
    wiring_generation: str,
) -> ActuationResult:
    """Send BatchLinkDown to Node Agents and publish LinkDown proof events."""
    sorted_pairs = sorted(pairs)
    plan = build_link_down_batch_plan(
        pairs=sorted_pairs,
        actual_links=actual_links,
        locator=locator,
        gs_capacities=gs_capacities,
    )
    for node_a, node_b in plan.skipped_unscheduled:
        log.warning("Skipping DOWN %s-%s: pod(s) not yet scheduled", node_a, node_b)

    agent_results: list[AgentCommandResult] = []
    agent_addrs = list(plan.agent_ifaces.keys())
    if agent_addrs:
        agent_results = list(
            await asyncio.gather(
                *[
                    _send_batch_down_to_agent(
                        addr=addr,
                        interfaces=plan.agent_ifaces[addr],
                        pool=pool,
                        sim_iso=sim_iso,
                        session_id=session_id,
                        wiring_generation=wiring_generation,
                    )
                    for addr in agent_addrs
                ]
            )
        )

    pair_link_type = {
        pair: actual_links[pair].link_type for pair in sorted_pairs if pair in actual_links
    }
    pair_gs_id = {
        pair: _gs_id_for_pair(pair, gs_capacities, pair_link_type.get(pair, ""))
        for pair in sorted_pairs
    }
    result = build_actuation_result(
        operation="BatchLinkDown",
        requested_pairs=sorted_pairs,
        pair_agent_ifaces=plan.pair_agent_ifaces,
        pair_link_type=pair_link_type,
        pair_gs_id=pair_gs_id,
        agent_results=agent_results,
    )

    now = datetime.now(UTC)
    for pair in sorted(result.succeeded_pairs):
        info = actual_links.get(pair)
        if not info:
            continue
        event = LinkDown(
            sim_time=sim_time,
            wall_time=now,
            node_a=pair[0],
            node_b=pair[1],
            interface_a=info.interface_a,
            interface_b=info.interface_b,
            reason=down_reasons.get(pair, "vis_lost"),
            link_type=info.link_type,
        )
        await js.publish(subj_link_down, event.model_dump_json().encode())
    return result


async def send_batch_up(
    *,
    pairs: set[LinkPair],
    desired: Mapping[LinkPair, ActiveLinkInfo],
    locator: Any,
    pool: Any,
    js: Any,
    subj_link_up: str,
    sim_iso: str,
    sim_time: datetime,
    gs_capacities: Mapping[str, int],
    latency_compensation: LatencyCompensationFn,
    validate_authority_freshness: AuthorityFreshnessValidator,
    link_provenance: LinkProvenanceBuilder,
    session_id: str,
    wiring_generation: str,
) -> ActuationResult:
    """Send BatchLinkUp to Node Agents and publish LinkUp proof events."""
    sorted_pairs = sorted(pairs)
    for pair in sorted_pairs:
        info = desired.get(pair)
        if info is None:
            raise RuntimeError(f"Dispatch planner requested LinkUp for missing desired pair {pair}")
        validate_authority_freshness(pair, info, sim_time, operation="LinkUp")

    plan = build_link_up_batch_plan(
        pairs=sorted_pairs,
        desired=desired,
        locator=locator,
        gs_capacities=gs_capacities,
        compensation_for_pair=latency_compensation,
    )

    agent_results: list[AgentCommandResult] = []
    agent_addrs = list(plan.agent_ifaces.keys())
    if agent_addrs:
        agent_results = list(
            await asyncio.gather(
                *[
                    _send_batch_up_to_agent(
                        addr=addr,
                        interfaces=plan.agent_ifaces[addr],
                        pool=pool,
                        sim_iso=sim_iso,
                        session_id=session_id,
                        wiring_generation=wiring_generation,
                    )
                    for addr in agent_addrs
                ]
            )
        )

    pair_link_type = {pair: desired[pair].link_type for pair in sorted_pairs}
    pair_gs_id = {
        pair: _gs_id_for_pair(pair, gs_capacities, pair_link_type[pair]) for pair in sorted_pairs
    }
    result = build_actuation_result(
        operation="BatchLinkUp",
        requested_pairs=sorted_pairs,
        pair_agent_ifaces=plan.pair_agent_ifaces,
        pair_link_type=pair_link_type,
        pair_gs_id=pair_gs_id,
        agent_results=agent_results,
    )

    now = datetime.now(UTC)
    for pair in sorted(result.succeeded_pairs):
        info = desired[pair]
        validate_authority_freshness(pair, info, sim_time, operation="LinkUp")
        if info.range_km is None:
            raise ValueError(f"ActiveLinkInfo for {pair} is missing OME-authoritative range_km")
        event = LinkUp(
            sim_time=sim_time,
            wall_time=now,
            node_a=pair[0],
            node_b=pair[1],
            interface_a=info.interface_a,
            interface_b=info.interface_b,
            latency_ms=info.latency_ms,
            bandwidth_mbps=info.bandwidth_mbps,
            range_km=info.range_km,
            reason="vis_gained",
            link_type=info.link_type,
            provenance=link_provenance(info, plan.pair_compensation[pair], sim_time),
        )
        await js.publish(subj_link_up, event.model_dump_json().encode())
    return result


async def send_authoritative_latency_updates(
    *,
    pairs: set[LinkPair],
    desired: Mapping[LinkPair, ActiveLinkInfo],
    locator: Any,
    pool: Any,
    js: Any,
    subj_latency: str,
    sim_time: datetime,
    gs_capacities: Mapping[str, int],
    latency_compensation: LatencyCompensationFn,
    validate_authority_freshness: AuthorityFreshnessValidator,
    link_provenance: LinkProvenanceBuilder,
    session_id: str,
    wiring_generation: str,
) -> ActuationResult:
    """Apply OME-authoritative latency changes for already-active links."""
    agent_entries: dict[str, list[node_agent_pb2.LatencyEntry]] = {}
    pair_compensation: dict[LinkPair, LatencyCompensation] = {}
    pair_agent_ifaces: dict[LinkPair, set[InterfaceAck]] = {}
    pair_link_type: dict[LinkPair, str] = {}
    pair_gs_id: dict[LinkPair, str | None] = {}
    now = datetime.now(UTC)
    sorted_pairs = sorted(pairs)

    def _add_entry(pair_key: LinkPair, agent: str, entry: node_agent_pb2.LatencyEntry) -> None:
        agent_entries.setdefault(agent, []).append(entry)
        pair_agent_ifaces.setdefault(pair_key, set()).add(
            (agent, entry.node_id, entry.interface_name)
        )

    for pair in sorted_pairs:
        info = desired[pair]
        pair_link_type[pair] = info.link_type
        pair_gs_id[pair] = _gs_id_for_pair(pair, gs_capacities, info.link_type)
        node_a, node_b = pair
        validate_authority_freshness(pair, info, sim_time, operation="LatencyUpdate")
        compensation = latency_compensation(node_a, node_b, info.latency_ms)
        pair_compensation[pair] = compensation
        netem_ms = compensation.netem_one_way_ms

        if info.link_type == "ground":
            gs_id = node_a if node_a in gs_capacities else node_b
            sat_id = node_b if node_a in gs_capacities else node_a
            gs_iface = info.interface_a if node_a in gs_capacities else info.interface_b
            sat_iface = info.interface_b if node_a in gs_capacities else info.interface_a
            locality = locator.link_locality(node_a, node_b)
            if locality is None:
                raise RuntimeError(
                    f"Cannot update ground latency for {node_a}<->{node_b}: pod placement unknown"
                )
            endpoint_agents = [(sat_id, sat_iface, locator.agent_addr(sat_id))]
            if locality == node_agent_pb2.LOCALITY_LOCAL:
                endpoint_agents.append((gs_id, gs_iface, locator.agent_addr(sat_id)))
            else:
                endpoint_agents.append((gs_id, gs_iface, locator.agent_addr(gs_id)))
            for endpoint_id, endpoint_iface, agent in endpoint_agents:
                _add_entry(
                    pair,
                    agent,
                    node_agent_pb2.LatencyEntry(
                        node_id=endpoint_id,
                        interface_name=endpoint_iface,
                        latency_ms=netem_ms,
                        link_type=node_agent_pb2.LINK_TYPE_GROUND,
                        gs_id=gs_id,
                        sat_id=sat_id,
                    ),
                )
        else:
            for nid, ifname in [(node_a, info.interface_a), (node_b, info.interface_b)]:
                _add_entry(
                    pair,
                    locator.agent_addr(nid),
                    node_agent_pb2.LatencyEntry(
                        node_id=nid,
                        interface_name=ifname,
                        latency_ms=netem_ms,
                        link_type=node_agent_pb2.LINK_TYPE_ISL,
                    ),
                )

    agent_results: list[AgentCommandResult] = []
    if agent_entries:
        agent_results = list(
            await asyncio.gather(
                *[
                    _send_latency_to_agent(
                        agent_addr=agent_addr,
                        entries=agent_entries[agent_addr],
                        pool=pool,
                        sim_time=sim_time,
                        session_id=session_id,
                        wiring_generation=wiring_generation,
                    )
                    for agent_addr in agent_entries
                ]
            )
        )
    result = build_actuation_result(
        operation="SetLatency",
        requested_pairs=sorted_pairs,
        pair_agent_ifaces=pair_agent_ifaces,
        pair_link_type=pair_link_type,
        pair_gs_id=pair_gs_id,
        agent_results=agent_results,
    )

    for pair in sorted(result.succeeded_pairs):
        info = desired[pair]
        if info.range_km is None:
            raise ValueError(f"Desired latency update for {pair} has no range_km")
        event = LatencyUpdate(
            sim_time=sim_time,
            wall_time=now,
            node_a=pair[0],
            node_b=pair[1],
            latency_ms=info.latency_ms,
            range_km=info.range_km,
            provenance=link_provenance(info, pair_compensation[pair], sim_time),
        )
        await js.publish(subj_latency, event.model_dump_json().encode())
    return result
