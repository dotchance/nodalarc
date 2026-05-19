# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Scheduler Node Agent dispatch actuator.

The Dispatcher owns event ordering and state mutation. This module owns the
side-effectful boundary to Node Agent BatchLinkUp/Down and SetLatency plus the
LinkUp/LinkDown/LatencyUpdate events that prove what was applied.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from nodalarc.models.link_events import LatencyUpdate, LinkDown, LinkUp
from nodalarc.proto import node_agent_pb2

from scheduler.desired_state import ActiveLinkInfo
from scheduler.latency_compensator import LatencyCompensation
from scheduler.node_agent_batches import (
    build_link_down_batch_plan,
    build_link_up_batch_plan,
    successful_interface_acks,
)

log = logging.getLogger(__name__)

LinkPair = tuple[str, str]
LatencyCompensationFn = Callable[[str, str, float], LatencyCompensation]
AuthorityFreshnessValidator = Callable[..., None]
LinkProvenanceBuilder = Callable[..., object]

# NATS request/reply is the transport envelope, not the proof store. Keep each
# proof-bearing Node Agent command well below the default NATS max_payload while
# preserving exact per-interface ACK validation.
MAX_NODE_AGENT_INTERFACES_PER_COMMAND = 64


def _chunks(items: list, size: int = MAX_NODE_AGENT_INTERFACES_PER_COMMAND):
    for start in range(0, len(items), size):
        yield start // size, items[start : start + size]


def _chunked_operation_id(base: str, chunk_index: int, chunk_count: int) -> str:
    if chunk_count == 1:
        return base
    return f"{base}-part{chunk_index + 1:03d}of{chunk_count:03d}"


async def _send_batch_down_to_agent(
    *,
    addr: str,
    interfaces: list[node_agent_pb2.InterfaceDown],
    pool: Any,
    sim_iso: str,
    session_id: str,
    wiring_generation: str,
) -> set[tuple[str, str, str]]:
    successful_ifaces: set[tuple[str, str, str]] = set()
    chunks = list(_chunks(interfaces))
    stub = pool.get_stub(addr)
    for chunk_index, chunk in chunks:
        req = node_agent_pb2.BatchLinkDownRequest(
            envelope=node_agent_pb2.CommandEnvelope(
                operation_id=_chunked_operation_id(
                    f"{sim_iso}-down-{addr}", chunk_index, len(chunks)
                ),
                session_id=session_id,
                wiring_generation=wiring_generation,
                operation_kind="BatchLinkDown",
            ),
            target_sim_time=sim_iso,
            interfaces=chunk,
        )
        result = await stub.async_batch_link_down(req)
        successful_ifaces.update(
            successful_interface_acks(
                result=result,
                requested_interfaces=chunk,
                agent_addr=addr,
                operation="BatchLinkDown",
            )
        )
        if not result.success:
            log.error(
                "BatchLinkDown partial failure on agent %s: %s",
                addr,
                result.error_message[:200],
            )
        else:
            log.debug(
                "BatchLinkDown: %d downed in %.1fms",
                result.interfaces_downed,
                result.apply_time_ms,
            )
    return successful_ifaces


async def _send_batch_up_to_agent(
    *,
    addr: str,
    interfaces: list[node_agent_pb2.InterfaceUp],
    pool: Any,
    sim_iso: str,
    session_id: str,
    wiring_generation: str,
) -> set[tuple[str, str, str]]:
    successful_ifaces: set[tuple[str, str, str]] = set()
    chunks = list(_chunks(interfaces))
    stub = pool.get_stub(addr)
    for chunk_index, chunk in chunks:
        req = node_agent_pb2.BatchLinkUpRequest(
            envelope=node_agent_pb2.CommandEnvelope(
                operation_id=_chunked_operation_id(
                    f"{sim_iso}-up-{addr}", chunk_index, len(chunks)
                ),
                session_id=session_id,
                wiring_generation=wiring_generation,
                operation_kind="BatchLinkUp",
            ),
            target_sim_time=sim_iso,
            interfaces=chunk,
        )
        result = await stub.async_batch_link_up(req)
        successful_ifaces.update(
            successful_interface_acks(
                result=result,
                requested_interfaces=chunk,
                agent_addr=addr,
                operation="BatchLinkUp",
            )
        )
        if not result.success:
            log.error(
                "BatchLinkUp partial failure on agent %s: %d upped: %s",
                addr,
                result.interfaces_upped,
                result.error_message[:200],
            )
        else:
            log.debug(
                "BatchLinkUp: %d upped in %.1fms",
                result.interfaces_upped,
                result.apply_time_ms,
            )
    return successful_ifaces


async def _send_latency_to_agent(
    *,
    agent_addr: str,
    entries: list[node_agent_pb2.LatencyEntry],
    pool: Any,
    sim_time: datetime,
    session_id: str,
    wiring_generation: str,
) -> None:
    chunks = list(_chunks(entries))
    stub = pool.get_stub(agent_addr)
    for chunk_index, chunk in chunks:
        req = node_agent_pb2.SetLatencyRequest(
            envelope=node_agent_pb2.CommandEnvelope(
                operation_id=_chunked_operation_id(
                    f"{sim_time.isoformat()}-latency-{agent_addr}",
                    chunk_index,
                    len(chunks),
                ),
                session_id=session_id,
                wiring_generation=wiring_generation,
                operation_kind="SetLatency",
            ),
            entries=chunk,
        )
        result = await stub.async_set_latency(req)
        if not result.success:
            raise RuntimeError(
                f"SetLatency rejected by agent {agent_addr}: {result.error_message[:200]}"
            )
        requested = {(entry.node_id, entry.interface_name) for entry in chunk}
        returned = {(entry.node_id, entry.interface_name) for entry in result.entry_results}
        if requested != returned:
            raise RuntimeError(
                f"SetLatency response from {agent_addr} did not identify every entry: "
                f"requested={sorted(requested)} returned={sorted(returned)}"
            )
        if not all(
            entry.success and entry.verified and not entry.dirty_kernel
            for entry in result.entry_results
        ):
            raise RuntimeError(
                f"SetLatency proof failed for agent {agent_addr}: {result.error_message[:200]}"
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
) -> set[LinkPair]:
    """Send BatchLinkDown to Node Agents and publish LinkDown proof events."""
    # Sort for deterministic gRPC batch ordering and NATS event publication.
    # Without this, set iteration order varies with PYTHONHASHSEED, producing
    # different LinkDown event sequences from identical reconcile decisions.
    sorted_pairs = sorted(pairs)
    plan = build_link_down_batch_plan(
        pairs=sorted_pairs,
        actual_links=actual_links,
        locator=locator,
        gs_capacities=gs_capacities,
    )
    for node_a, node_b in plan.skipped_unscheduled:
        log.warning("Skipping DOWN %s-%s: pod(s) not yet scheduled", node_a, node_b)

    successful_ifaces: set[tuple[str, str, str]] = set()
    agent_addrs = list(plan.agent_ifaces.keys())
    if agent_addrs:
        results = await asyncio.gather(
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
        for result in results:
            successful_ifaces.update(result)

    removed: set[LinkPair] = set()
    now = datetime.now(UTC)
    for pair in sorted_pairs:
        expected_ifaces = plan.pair_agent_ifaces.get(pair, set())
        if expected_ifaces and expected_ifaces <= successful_ifaces:
            removed.add(pair)
            info = actual_links.get(pair)
            if info:
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
                try:
                    await js.publish(subj_link_down, event.model_dump_json().encode())
                except Exception as exc:
                    log.error("Failed to publish LinkDown for %s: %s", pair, exc)
                    raise

    return removed


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
) -> set[LinkPair]:
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

    successful_ifaces: set[tuple[str, str, str]] = set()
    agent_addrs = list(plan.agent_ifaces.keys())
    if agent_addrs:
        results = await asyncio.gather(
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
        for result in results:
            successful_ifaces.update(result)

    added: set[LinkPair] = set()
    now = datetime.now(UTC)
    for pair in sorted_pairs:
        expected_ifaces = plan.pair_agent_ifaces.get(pair, set())
        if expected_ifaces and expected_ifaces <= successful_ifaces:
            added.add(pair)
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
            try:
                await js.publish(subj_link_up, event.model_dump_json().encode())
            except Exception as exc:
                log.error("Failed to publish LinkUp for %s: %s", pair, exc)
                raise

    return added


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
) -> set[LinkPair]:
    """Apply OME-authoritative latency changes for already-active links."""
    agent_entries: dict[str, list[node_agent_pb2.LatencyEntry]] = {}
    pair_compensation: dict[LinkPair, LatencyCompensation] = {}
    now = datetime.now(UTC)
    sorted_pairs = sorted(pairs)

    for pair in sorted_pairs:
        info = desired[pair]
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

            # Ground LinkUp shapes both GS and satellite pod interfaces. Latency
            # updates must therefore update both qdiscs as well; updating only
            # the satellite side leaves stale delay on the GS namespace.
            endpoint_agents = [(sat_id, sat_iface, locator.agent_addr(sat_id))]
            if locality == node_agent_pb2.LOCALITY_LOCAL:
                endpoint_agents.append((gs_id, gs_iface, locator.agent_addr(sat_id)))
            else:
                endpoint_agents.append((gs_id, gs_iface, locator.agent_addr(gs_id)))

            for endpoint_id, endpoint_iface, agent in endpoint_agents:
                agent_entries.setdefault(agent, []).append(
                    node_agent_pb2.LatencyEntry(
                        node_id=endpoint_id,
                        interface_name=endpoint_iface,
                        latency_ms=netem_ms,
                        link_type=node_agent_pb2.LINK_TYPE_GROUND,
                        gs_id=gs_id,
                        sat_id=sat_id,
                    )
                )
        else:
            for nid, ifname in [
                (node_a, info.interface_a),
                (node_b, info.interface_b),
            ]:
                agent = locator.agent_addr(nid)
                agent_entries.setdefault(agent, []).append(
                    node_agent_pb2.LatencyEntry(
                        node_id=nid,
                        interface_name=ifname,
                        latency_ms=netem_ms,
                        link_type=node_agent_pb2.LINK_TYPE_ISL,
                    )
                )

    tasks = []
    agent_addrs = list(agent_entries.keys())
    for agent_addr in agent_addrs:
        tasks.append(
            _send_latency_to_agent(
                agent_addr=agent_addr,
                entries=agent_entries[agent_addr],
                pool=pool,
                sim_time=sim_time,
                session_id=session_id,
                wiring_generation=wiring_generation,
            )
        )

    if tasks:
        await asyncio.gather(*tasks)

    for pair in sorted_pairs:
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

    return set(pairs)
