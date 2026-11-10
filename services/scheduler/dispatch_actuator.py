# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""Scheduler Node Agent dispatch actuator.

The Dispatcher owns event ordering and state mutation. This module owns the
side-effectful boundary to Node Agent BatchLinkUp/Down, SetLatency, and
read-only KernelInventory verification. Proof details are preserved instead of
collapsing Node Agent responses to a bare success set.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from nodalarc.models.link_events import (
    LatencyUpdate,
    LinkDown,
    LinkEndCloseCause,
    LinkEndLoss,
    LinkUp,
)
from nodalarc.models.resolved_session import InterfaceRates
from nodalarc.models.scheduler_ops import ActuationFailureClass
from nodalarc.proto import node_agent_pb2
from nodalarc.vxlan import compute_vni

from scheduler.actuation import (
    ActuationResult,
    AgentCommandResult,
    EndLossCounts,
    InterfaceAck,
    build_actuation_result,
    classify_agent_exception,
    classify_agent_response,
)
from scheduler.contact_drain import remaining_ends
from scheduler.desired_state import ActiveLinkInfo, StageLoss
from scheduler.dispatch_planner import gs_id_for_pair
from scheduler.latency_compensator import LatencyCompensation, egress_ms
from scheduler.node_agent_batches import (
    LinkUpBatchPlan,
    build_link_down_batch_plan,
    build_link_up_batch_plan,
    required_ground_endpoints,
    restage_link_up_plan,
    terminal_rates_message,
)

log = logging.getLogger(__name__)


def _log_actuation_latency(
    *,
    operation: str,
    pair_count: int,
    agent_count: int,
    elapsed_ms: float,
    result: ActuationResult,
) -> None:
    """Emit the wall-clock dispatch->ACK latency for one actuator batch.

    This is the raw per-operation actuation latency (BatchLinkUp/Down/SetLatency),
    measured in WALL-CLOCK around the Node Agent RPC round-trip (commit+dispatch ->
    proof), NOT sim-time. Aggregate p50/p95/p99 per operation from debug logs
    during targeted measurement runs to justify or tighten the actuation-latency
    contract (platform scheduler fault-after bound, the in_flight -> faulted bound)
    against real data instead of guessing. Logs at WARNING when the batch had
    failures so a slow/failed actuation is loud, DEBUG otherwise.
    """
    level = logging.WARNING if result.failed_pairs else logging.DEBUG
    log.log(
        level,
        "Actuation latency op=%s pairs=%d agents=%d elapsed_ms=%.1f succeeded=%d failed=%d",
        operation,
        pair_count,
        agent_count,
        elapsed_ms,
        len(result.succeeded_pairs),
        len(result.failed_pairs),
        extra={
            "event": "actuation_latency",
            "operation": operation,
            "pair_count": pair_count,
            "agent_count": agent_count,
            "actuation_latency_ms": elapsed_ms,
            "succeeded": len(result.succeeded_pairs),
            "failed": len(result.failed_pairs),
        },
    )


LinkPair = tuple[str, str]
LatencyCompensationFn = Callable[[str, str, tuple[float, float]], LatencyCompensation]

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
    """The station of a ground pair, None for any other link type."""
    return gs_id_for_pair(pair, gs_capacities) if link_type == "ground" else None


async def _send_chunked_command(
    *,
    addr: str,
    items: list,
    pool: Any,
    operation: str,
    operation_id_base: str,
    session_id: str,
    wiring_generation: str,
    writer_epoch: int,
    build_request: Callable[[node_agent_pb2.CommandEnvelope, list], Any],
    send: Callable[[Any, Any], Awaitable[Any]],
) -> AgentCommandResult:
    """Send one operation to one agent, chunk by chunk in order, and merge.

    Each chunk is fenced by the same session, wiring generation and writer
    epoch and named by `_chunked_operation_id`. Only the send and the reply's classification
    sit inside the failure boundary: a stub lookup or request construction
    error propagates, and so does cancellation. A chunk classified as failed
    does not stop the chunks after it; every chunk's evidence reaches the
    merged result.
    """
    chunks = list(_chunks(items))
    chunk_results: list[AgentCommandResult] = []
    stub = pool.get_stub(addr)
    for chunk_index, chunk in chunks:
        req = build_request(
            node_agent_pb2.CommandEnvelope(
                operation_id=_chunked_operation_id(operation_id_base, chunk_index, len(chunks)),
                session_id=session_id,
                wiring_generation=wiring_generation,
                writer_epoch=writer_epoch,
                operation_kind=operation,
            ),
            chunk,
        )
        try:
            result = await send(stub, req)
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


async def _send_batch_down_to_agent(
    *,
    addr: str,
    interfaces: list[node_agent_pb2.InterfaceDown],
    pool: Any,
    sim_iso: str,
    session_id: str,
    wiring_generation: str,
    writer_epoch: int,
) -> AgentCommandResult:
    return await _send_chunked_command(
        addr=addr,
        items=interfaces,
        pool=pool,
        operation="BatchLinkDown",
        operation_id_base=f"{sim_iso}-down-{addr}",
        session_id=session_id,
        wiring_generation=wiring_generation,
        writer_epoch=writer_epoch,
        build_request=lambda envelope, chunk: node_agent_pb2.BatchLinkDownRequest(
            envelope=envelope, target_sim_time=sim_iso, interfaces=chunk
        ),
        send=lambda stub, req: stub.async_batch_link_down(req),
    )


async def _send_batch_up_to_agent(
    *,
    addr: str,
    interfaces: list[node_agent_pb2.InterfaceUp],
    pool: Any,
    sim_iso: str,
    session_id: str,
    wiring_generation: str,
    writer_epoch: int,
    label: str,
) -> AgentCommandResult:
    return await _send_chunked_command(
        addr=addr,
        items=interfaces,
        pool=pool,
        operation="BatchLinkUp",
        operation_id_base=f"{sim_iso}-{label}-{addr}",
        session_id=session_id,
        wiring_generation=wiring_generation,
        writer_epoch=writer_epoch,
        build_request=lambda envelope, chunk: node_agent_pb2.BatchLinkUpRequest(
            envelope=envelope, target_sim_time=sim_iso, interfaces=chunk
        ),
        send=lambda stub, req: stub.async_batch_link_up(req),
    )


async def _send_latency_to_agent(
    *,
    agent_addr: str,
    entries: list[node_agent_pb2.LatencyEntry],
    pool: Any,
    sim_time: datetime,
    session_id: str,
    wiring_generation: str,
    writer_epoch: int,
) -> AgentCommandResult:
    return await _send_chunked_command(
        addr=agent_addr,
        items=entries,
        pool=pool,
        operation="SetLatency",
        operation_id_base=f"{sim_time.isoformat()}-latency-{agent_addr}",
        session_id=session_id,
        wiring_generation=wiring_generation,
        writer_epoch=writer_epoch,
        build_request=lambda envelope, chunk: node_agent_pb2.SetLatencyRequest(
            envelope=envelope, entries=chunk
        ),
        send=lambda stub, req: stub.async_set_latency(req),
    )


def _merge_agent_results(
    *, addr: str, operation: str, results: list[AgentCommandResult]
) -> AgentCommandResult:
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
            writer_fenced=False,
            details={"agent_addr": addr, "operation": operation, "chunks": []},
        )

    if len(results) == 1:
        # The common case: one chunk. Pass the classified result through
        # verbatim so its failure class and per-interface diagnostics
        # (requested/returned/interface_results) survive to the ops events.
        return results[0]

    # Every failure class a chunk can carry must appear here; a class missing
    # from the precedence silently merges to NONE, which both hides the
    # failure from consumers and defeats transport-only detection.
    precedence = [
        ActuationFailureClass.FENCE,
        ActuationFailureClass.GROUND_KERNEL_DIRTY,
        ActuationFailureClass.GROUND_UNKNOWN,
        ActuationFailureClass.AGENT_UNREACHABLE,
        ActuationFailureClass.GROUND_CLEAN_FAILURE,
        ActuationFailureClass.ISL_FAILURE,
    ]
    failure_class = ActuationFailureClass.NONE
    for candidate in precedence:
        if any(result.failure_class == candidate for result in results):
            failure_class = candidate
            break
    unmerged = {
        result.failure_class
        for result in results
        if result.failure_class != ActuationFailureClass.NONE
        and result.failure_class not in precedence
    }
    if unmerged:
        raise RuntimeError(
            f"_merge_agent_results cannot merge failure classes {sorted(unmerged)}; "
            "extend the precedence list"
        )

    def _concat(key: str) -> list:
        merged: list = []
        for result in results:
            merged.extend(result.details.get(key) or [])
        return merged

    return AgentCommandResult(
        agent_addr=addr,
        operation=operation,
        requested=tuple(item for result in results for item in result.requested),
        success_acks=frozenset().union(*(result.success_acks for result in results)),
        failure_class=failure_class,
        dirty_kernel=any(result.dirty_kernel for result in results),
        unknown_outcome=any(result.unknown_outcome for result in results),
        fence_failure=any(result.fence_failure for result in results),
        writer_fenced=any(result.writer_fenced for result in results),
        details={
            "agent_addr": addr,
            "operation": operation,
            "success": all(bool(result.details.get("success")) for result in results),
            "error_code": next(
                (
                    result.details.get("error_code")
                    for result in results
                    if result.failure_class != ActuationFailureClass.NONE
                ),
                "NODE_AGENT_ERROR_UNSPECIFIED",
            ),
            "error_message": "; ".join(
                msg for result in results if (msg := str(result.details.get("error_message") or ""))
            ),
            "dirty_kernel": any(result.dirty_kernel for result in results),
            "unknown_outcome": any(result.unknown_outcome for result in results),
            "fence_failure": any(result.fence_failure for result in results),
            "writer_fenced": any(result.writer_fenced for result in results),
            "requested": _concat("requested"),
            "returned": _concat("returned"),
            "interface_results": _concat("interface_results"),
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
    interface_rates: Mapping[tuple[str, str], InterfaceRates],
) -> tuple[dict[str, list[node_agent_pb2.KernelInventoryEntry]], set[InterfaceAck]]:
    if info.link_type != "ground":
        raise ValueError(f"KernelInventory is ground-only; got {pair} type={info.link_type!r}")
    node_a, node_b = pair
    gs_id, sat_id, gs_iface, sat_iface = required_ground_endpoints(pair, info, gs_capacities)
    locality = locator.link_locality(node_a, node_b)
    vni = (
        compute_vni(gs_id, sat_id, gs_iface, sat_iface)
        if locality == node_agent_pb2.LOCALITY_CROSS_NODE
        else 0
    )
    commanded = (0.0, 0.0)
    if expected_admin_up:
        # Prove the kernel against what was COMMANDED, never against a live
        # recomputation: compensation reads measured substrate RTT, which
        # drifts between dispatch and proof, and that drift is not kernel
        # divergence. A link is only proven up after this Scheduler commanded
        # it, and dispatch records the commanded delay on its info.
        if info.commanded_netem_ms is None:
            raise ValueError(
                f"ground link {pair} is proven up with no commanded delay; "
                "only commanded links are proven"
            )
        commanded = info.commanded_netem_ms

    entries_by_agent: dict[str, list[node_agent_pb2.KernelInventoryEntry]] = {}
    ack_keys: set[InterfaceAck] = set()

    def _remote_ip(peer_node: str) -> str:
        if locality != node_agent_pb2.LOCALITY_CROSS_NODE:
            return ""
        return locator.node_ip(locator.k3s_node(peer_node))

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
            remote_node_ip=_remote_ip(peer_node),
            latency_ms=egress_ms(pair, node_id, commanded),
            expected_admin_up=expected_admin_up,
        )
        if locality == node_agent_pb2.LOCALITY_LOCAL:
            entry.peer_latency_ms = egress_ms(pair, peer_node, commanded)
        if expected_admin_up:
            # Each end is proven against its own terminal; a LOCAL entry
            # proves both ends on the one agent that owns them.
            entry.rates.CopyFrom(terminal_rates_message(interface_rates, node_id, iface))
            if locality == node_agent_pb2.LOCALITY_LOCAL:
                entry.peer_rates.CopyFrom(
                    terminal_rates_message(interface_rates, peer_node, peer_iface)
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
    writer_epoch: int,
    gs_id: str,
) -> AgentCommandResult:
    return await _send_chunked_command(
        addr=addr,
        items=entries,
        pool=pool,
        operation="KernelInventory",
        operation_id_base=f"{sim_iso}-kernel-inventory-{gs_id or 'long-delay'}-{addr}",
        session_id=session_id,
        wiring_generation=wiring_generation,
        writer_epoch=writer_epoch,
        build_request=lambda envelope, chunk: node_agent_pb2.KernelInventoryRequest(
            envelope=envelope, target_sim_time=sim_iso, gs_id=gs_id, entries=chunk
        ),
        send=lambda stub, req: stub.async_kernel_inventory(req),
    )


@dataclass(frozen=True, slots=True)
class GroundInventoryRequests:
    """The KernelInventory requests that prove one station's passes, by agent: station-scoped
    requests (``gs_id`` set) for local passes, requests naming no station for long-delay ends."""

    requests: tuple[tuple[str, str, tuple[node_agent_pb2.KernelInventoryEntry, ...]], ...]
    pair_agent_ifaces: dict[LinkPair, set[InterfaceAck]]


def ground_inventory_requests(
    *,
    gs_id: str,
    expected_up: Mapping[LinkPair, ActiveLinkInfo],
    expected_down: Mapping[LinkPair, ActiveLinkInfo],
    locator: Any,
    gs_capacities: Mapping[str, int],
    interface_rates: Mapping[tuple[str, str], InterfaceRates],
) -> GroundInventoryRequests:
    """The proof of each of a station's passes. A local pass is proven against the local ground
    layout (a delay queue on each pod interface). A long-delay pass that is up is proven by the
    long-delay inventory (``long_delay_inventory_entries``): its ends still carrying the contact
    hold it on their host-side veths, admission open or stopped, the transmit shaper and the
    received direction's delay queue. Rejected alternative: the local proof for every pass, which
    finds no pod-interface queue on a long-delay pass and marks its station dirty."""
    local_by_agent: dict[str, list[node_agent_pb2.KernelInventoryEntry]] = {}
    long_delay_by_agent: dict[str, list[node_agent_pb2.KernelInventoryEntry]] = {}
    pair_agent_ifaces: dict[LinkPair, set[InterfaceAck]] = {}
    for expected_admin_up, pairs in ((True, expected_up), (False, expected_down)):
        for pair, info in pairs.items():
            if _gs_id_for_pair(pair, gs_capacities, info.link_type) != gs_id:
                continue
            if expected_admin_up and info.contact is not None:
                agent_entries, ack_keys = long_delay_inventory_entries(
                    pair=pair,
                    info=info,
                    node_ids=remaining_ends(pair, info),
                    locator=locator,
                    interface_rates=interface_rates,
                    gs_capacities=gs_capacities,
                )
                by_agent = long_delay_by_agent
            else:
                agent_entries, ack_keys = _ground_inventory_entries_for_pair(
                    pair=pair,
                    info=info,
                    expected_admin_up=expected_admin_up,
                    locator=locator,
                    gs_capacities=gs_capacities,
                    interface_rates=interface_rates,
                )
                by_agent = local_by_agent
            pair_agent_ifaces.setdefault(pair, set()).update(ack_keys)
            for agent, entries in agent_entries.items():
                by_agent.setdefault(agent, []).extend(entries)
    # A station-scoped request proves local passes; a request naming no station proves
    # long-delay ends, as the long-delay audit sends it.
    requests = [
        (agent_addr, gs_id, tuple(entries))
        for agent_addr, entries in sorted(local_by_agent.items())
    ] + [
        (agent_addr, "", tuple(entries))
        for agent_addr, entries in sorted(long_delay_by_agent.items())
    ]
    return GroundInventoryRequests(requests=tuple(requests), pair_agent_ifaces=pair_agent_ifaces)


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
    interface_rates: Mapping[tuple[str, str], InterfaceRates],
    session_id: str,
    wiring_generation: str,
    writer_epoch: int,
) -> ActuationResult:
    """Read-only GS-facing kernel proof for the Scheduler actuation lifecycle: the station's
    periodic clean-state audit and the re-check of a station that is not clean. Each pass gets
    the proof of its class (``ground_inventory_requests``)."""
    del sim_time  # carried by the public signature for call-site symmetry and future audit fields
    planned = ground_inventory_requests(
        gs_id=gs_id,
        expected_up=expected_up,
        expected_down=expected_down,
        locator=locator,
        gs_capacities=gs_capacities,
        interface_rates=interface_rates,
    )
    pair_agent_ifaces = planned.pair_agent_ifaces
    pair_link_type = dict.fromkeys(pair_agent_ifaces, "ground")
    pair_gs_id: dict[LinkPair, str | None] = dict.fromkeys(pair_agent_ifaces, gs_id)
    agent_results: list[AgentCommandResult] = []
    if planned.requests:
        agent_results = list(
            await asyncio.gather(
                *[
                    _send_kernel_inventory_to_agent(
                        addr=agent_addr,
                        entries=list(entries),
                        pool=pool,
                        sim_iso=sim_iso,
                        session_id=session_id,
                        wiring_generation=wiring_generation,
                        writer_epoch=writer_epoch,
                        gs_id=request_gs_id,
                    )
                    for agent_addr, request_gs_id, entries in planned.requests
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


def _stage_loss(counts: EndLossCounts, *, stage: int, end: str) -> StageLoss:
    """What a long-delay end reported at OPEN or STOP. Each stage reports only its own counts;
    another count from it is a contract the Scheduler does not understand, and it refuses."""
    if stage == node_agent_pb2.LINK_UP_STAGE_OPEN:
        unexpected = counts.queue_dropped or counts.discarded_at_close
        unexpected = unexpected or counts.untransmitted_at_stop or counts.transmit_dropped
        loss = StageLoss(refused_before_open=counts.admission_taken)
    elif stage == node_agent_pb2.LINK_DOWN_STAGE_STOP_ADMISSION:
        unexpected = counts.admission_taken or counts.queue_dropped or counts.discarded_at_close
        loss = StageLoss(
            untransmitted_at_stop=counts.untransmitted_at_stop,
            transmit_dropped=counts.transmit_dropped,
        )
    else:
        raise ValueError(f"stage {stage} is not one whose loss waits for the end's close")
    if unexpected:
        raise ValueError(f"{end} reported counts its stage {stage} does not produce: {counts}")
    return loss


def record_stage_losses(
    result: ActuationResult, *, pair: LinkPair, info: ActiveLinkInfo, stage: int
) -> None:
    """Keep what each end of ``pair`` reported at its contact's OPEN or STOP on the contact's
    record, until the end's close or reopen completes the end's loss."""
    pair_ends = {(pair[0], info.interface_a), (pair[1], info.interface_b)}
    for (node_id, interface), counts in sorted(result.end_losses.items()):
        if (node_id, interface) not in pair_ends:
            continue
        reported = _stage_loss(counts, stage=stage, end=f"{node_id}/{interface}")
        info.stage_losses[node_id] = info.stage_losses.get(node_id, StageLoss()).plus(reported)


def end_loss_records(
    result: ActuationResult,
    *,
    pair: LinkPair,
    info: ActiveLinkInfo,
    cause: LinkEndCloseCause,
) -> tuple[LinkEndLoss, ...]:
    """What each end of ``pair`` that this result closed, or reopened for a new contact, lost
    during ``info``'s contact, with the cause: its close's counts and what it reported at the
    contact's OPEN and STOP. The Node Agent reports every count once, since its last report, so
    the counts add."""
    pair_ends = {(pair[0], info.interface_a), (pair[1], info.interface_b)}
    records = []
    for (node_id, interface), counts in sorted(result.end_losses.items()):
        if (node_id, interface) not in pair_ends:
            continue
        stage = info.stage_losses.get(node_id, StageLoss())
        records.append(
            LinkEndLoss(
                node_id=node_id,
                interface=interface,
                cause=cause,
                refused_before_open=stage.refused_before_open,
                untransmitted_at_stop=stage.untransmitted_at_stop + counts.untransmitted_at_stop,
                admission_taken=counts.admission_taken,
                transmit_dropped=stage.transmit_dropped + counts.transmit_dropped,
                queue_dropped=counts.queue_dropped,
                discarded_at_close=counts.discarded_at_close,
                discarded_exact=counts.discarded_exact,
            )
        )
    return tuple(records)


def stage_only_records(
    pair: LinkPair,
    info: ActiveLinkInfo,
    *,
    reported: frozenset[str],
    cause: LinkEndCloseCause,
) -> tuple[LinkEndLoss, ...]:
    """The record of each end of ``pair`` that reported losses at ``info``'s OPEN or STOP but
    neither closed nor reported again when its next contact was prepared: an end a restarted
    Scheduler stopped that already carried the contact being opened. ``reported`` names the ends
    whose records already exist."""
    interfaces = {pair[0]: info.interface_a, pair[1]: info.interface_b}
    return tuple(
        LinkEndLoss(
            node_id=node_id,
            interface=interfaces[node_id],
            cause=cause,
            refused_before_open=stage.refused_before_open,
            untransmitted_at_stop=stage.untransmitted_at_stop,
            admission_taken=0,
            transmit_dropped=stage.transmit_dropped,
            queue_dropped=0,
            discarded_at_close=0,
            discarded_exact=True,
        )
        for node_id, stage in sorted(info.stage_losses.items())
        if node_id not in reported
    )


async def survey_long_delay_ends(
    *,
    pairs: set[LinkPair],
    interface_map: Mapping[LinkPair, tuple[str, str]],
    locator: Any,
    pool: Any,
    sim_iso: str,
    session_id: str,
    wiring_generation: str,
    writer_epoch: int,
) -> ActuationResult:
    """Read what both ends of every long-delay link carry (KernelInventory with ``survey``): the
    contact, whether admission is stopped, the receive queue's delay. Changes nothing."""
    entries_by_agent: dict[str, list[node_agent_pb2.KernelInventoryEntry]] = {}
    pair_agent_ifaces: dict[LinkPair, set[InterfaceAck]] = {}
    requested = sorted(pairs)
    for pair in requested:
        node_a, node_b = pair
        interface_a, interface_b = interface_map[pair]
        locality = locator.link_locality(node_a, node_b)
        vni = (
            compute_vni(node_a, node_b, interface_a, interface_b)
            if locality == node_agent_pb2.LOCALITY_CROSS_NODE
            else 0
        )
        for nid, ifname, peer_nid, peer_ifname in (
            (node_a, interface_a, node_b, interface_b),
            (node_b, interface_b, node_a, interface_a),
        ):
            agent = locator.agent_addr(nid)
            entries_by_agent.setdefault(agent, []).append(
                node_agent_pb2.KernelInventoryEntry(
                    node_id=nid,
                    interface_name=ifname,
                    link_type=node_agent_pb2.LINK_TYPE_ISL,
                    peer_node_id=peer_nid,
                    peer_interface_name=peer_ifname,
                    locality=locality,
                    vni=vni,
                    remote_node_ip=(
                        locator.node_ip(locator.k3s_node(peer_nid))
                        if locality == node_agent_pb2.LOCALITY_CROSS_NODE
                        else ""
                    ),
                    survey=True,
                )
            )
            pair_agent_ifaces.setdefault(pair, set()).add((agent, nid, ifname))
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
                        writer_epoch=writer_epoch,
                        gs_id="",
                    )
                    for agent_addr in sorted(entries_by_agent)
                ]
            )
        )
    return build_actuation_result(
        operation="KernelInventory",
        requested_pairs=requested,
        pair_agent_ifaces=pair_agent_ifaces,
        pair_link_type=dict.fromkeys(requested, "isl"),
        pair_gs_id=dict.fromkeys(requested),
        agent_results=agent_results,
    )


async def survey_ground_terminals(
    *,
    terminals: list[tuple[str, str]],
    gs_capacities: Mapping[str, int],
    locator: Any,
    pool: Any,
    sim_iso: str,
    session_id: str,
    wiring_generation: str,
    writer_epoch: int,
) -> ActuationResult:
    """Read what each access terminal of a node with long-delay ground peers carries
    (KernelInventory with ``survey``): the contact, its peer end, whether admission is stopped,
    the receive queue's delay. The peer of a pass is what the survey finds, so each entry names
    its terminal and its side only. Results are keyed by (node id, interface). Changes nothing."""
    entries_by_agent: dict[str, list[node_agent_pb2.KernelInventoryEntry]] = {}
    keys: dict[tuple[str, str], set[InterfaceAck]] = {}
    for node_id, interface in sorted(terminals):
        agent = locator.agent_addr(node_id)
        station = node_id in gs_capacities
        entries_by_agent.setdefault(agent, []).append(
            node_agent_pb2.KernelInventoryEntry(
                node_id=node_id,
                interface_name=interface,
                link_type=node_agent_pb2.LINK_TYPE_GROUND,
                locality=node_agent_pb2.LOCALITY_LOCAL,
                gs_id=node_id if station else "",
                sat_id="" if station else node_id,
                survey=True,
            )
        )
        keys[(node_id, interface)] = {(agent, node_id, interface)}
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
                        writer_epoch=writer_epoch,
                        gs_id="",
                    )
                    for agent_addr in sorted(entries_by_agent)
                ]
            )
        )
    requested = sorted(keys)
    return build_actuation_result(
        operation="KernelInventory",
        requested_pairs=requested,
        pair_agent_ifaces=keys,
        pair_link_type=dict.fromkeys(requested, "ground"),
        pair_gs_id=dict.fromkeys(requested),
        agent_results=agent_results,
    )


async def publish_link_down(
    *,
    js: Any,
    subject: str,
    pair: LinkPair,
    info: ActiveLinkInfo,
    sim_time: datetime,
    reason: str,
    losses: tuple[LinkEndLoss, ...],
) -> None:
    """Record a link's teardown: every end of its contact closed."""
    event = LinkDown(
        sim_time=sim_time,
        wall_time=datetime.now(UTC),
        node_a=pair[0],
        node_b=pair[1],
        interface_a=info.interface_a,
        interface_b=info.interface_b,
        reason=reason,
        link_type=info.link_type,
        contact_id=info.contact.contact_id if info.contact is not None else None,
        losses=losses,
    )
    await js.publish(subject, event.model_dump_json().encode())


def long_delay_inventory_entries(
    *,
    pair: LinkPair,
    info: ActiveLinkInfo,
    node_ids: frozenset[str],
    locator: Any,
    interface_rates: Mapping[tuple[str, str], InterfaceRates],
    gs_capacities: Mapping[str, int],
) -> tuple[dict[str, list[node_agent_pb2.KernelInventoryEntry]], set[InterfaceAck]]:
    """KernelInventory entries proving the named ends of a long-delay contact in the stage the
    Scheduler holds it in: open (admission open) or draining (admission stopped); either way
    the contact on the host-side veth and the received direction's delay queue as commanded."""
    if info.contact is None:
        raise ValueError(f"{pair} is a local link; its ends have no contact stage")
    if info.commanded_netem_ms is None:
        raise ValueError(f"long-delay link {pair} has no commanded delay to prove")
    node_a, node_b = pair
    locality = locator.link_locality(node_a, node_b)
    cross = locality == node_agent_pb2.LOCALITY_CROSS_NODE
    ground: dict[str, str] = {}
    if info.link_type == "ground":
        # A ground pass's tunnel is named from its station and satellite, as its LinkUp names it.
        gs_id, sat_id, gs_iface, sat_iface = required_ground_endpoints(pair, info, gs_capacities)
        ground = {"gs_id": gs_id, "sat_id": sat_id}
        vni = compute_vni(gs_id, sat_id, gs_iface, sat_iface) if cross else 0
    else:
        vni = compute_vni(node_a, node_b, info.interface_a, info.interface_b) if cross else 0
    link_type = (
        node_agent_pb2.LINK_TYPE_GROUND
        if info.link_type == "ground"
        else node_agent_pb2.LINK_TYPE_ISL
    )
    entries_by_agent: dict[str, list[node_agent_pb2.KernelInventoryEntry]] = {}
    ack_keys: set[InterfaceAck] = set()
    for nid, ifname, peer_nid, peer_ifname in [
        (node_a, info.interface_a, node_b, info.interface_b),
        (node_b, info.interface_b, node_a, info.interface_a),
    ]:
        if nid not in node_ids:
            continue
        agent = locator.agent_addr(nid)
        entry = node_agent_pb2.KernelInventoryEntry(
            node_id=nid,
            interface_name=ifname,
            link_type=link_type,
            peer_node_id=peer_nid,
            peer_interface_name=peer_ifname,
            locality=locality,
            vni=vni,
            remote_node_ip=(
                locator.node_ip(locator.k3s_node(peer_nid))
                if locality == node_agent_pb2.LOCALITY_CROSS_NODE
                else ""
            ),
            **ground,
            expected_admin_up=True,
            contact_id=info.end_contact_id(nid),
            receive_latency_ms=egress_ms(pair, peer_nid, info.commanded_netem_ms),
            expected_admission_stopped=info.drain is not None,
        )
        entry.rates.CopyFrom(terminal_rates_message(interface_rates, nid, ifname))
        # The peer's transmit rate sizes the receive queue the proof checks.
        entry.peer_rates.CopyFrom(terminal_rates_message(interface_rates, peer_nid, peer_ifname))
        entries_by_agent.setdefault(agent, []).append(entry)
        ack_keys.add((agent, nid, ifname))
    return entries_by_agent, ack_keys


async def verify_long_delay_ends(
    *,
    ends: Mapping[LinkPair, frozenset[str]],
    actual_links: Mapping[LinkPair, ActiveLinkInfo],
    locator: Any,
    pool: Any,
    sim_iso: str,
    interface_rates: Mapping[tuple[str, str], InterfaceRates],
    gs_capacities: Mapping[str, int],
    session_id: str,
    wiring_generation: str,
    writer_epoch: int,
) -> ActuationResult:
    """Read-only proof of long-delay contact ends in the stage the Scheduler holds them in."""
    entries_by_agent: dict[str, list[node_agent_pb2.KernelInventoryEntry]] = {}
    pair_agent_ifaces: dict[LinkPair, set[InterfaceAck]] = {}
    for pair, node_ids in sorted(ends.items()):
        agent_entries, ack_keys = long_delay_inventory_entries(
            pair=pair,
            info=actual_links[pair],
            node_ids=node_ids,
            locator=locator,
            interface_rates=interface_rates,
            gs_capacities=gs_capacities,
        )
        pair_agent_ifaces[pair] = ack_keys
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
                        writer_epoch=writer_epoch,
                        gs_id="",
                    )
                    for agent_addr in sorted(entries_by_agent)
                ]
            )
        )
    requested = sorted(ends)
    return build_actuation_result(
        operation="KernelInventory",
        requested_pairs=requested,
        pair_agent_ifaces=pair_agent_ifaces,
        pair_link_type={pair: actual_links[pair].link_type for pair in requested},
        pair_gs_id={
            pair: _gs_id_for_pair(pair, gs_capacities, actual_links[pair].link_type)
            for pair in requested
        },
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
    writer_epoch: int,
    stage: int,
    ends: Mapping[LinkPair, frozenset[str]] | None = None,
    contact_ids: Mapping[tuple[str, str], str] | None = None,
) -> ActuationResult:
    """Send BatchLinkDown to Node Agents.

    A local link closes at once, and its LinkDown is published here with what each end's queue
    held (a sample). A long-delay contact's stages are recorded by the Dispatcher, which
    publishes its LinkDown when the last end closes.
    """
    sorted_pairs = sorted(pairs)
    plan = build_link_down_batch_plan(
        pairs=sorted_pairs,
        actual_links=actual_links,
        locator=locator,
        gs_capacities=gs_capacities,
        stage=stage,
        ends=ends,
        contact_ids=contact_ids,
    )
    agent_results: list[AgentCommandResult] = []
    agent_addrs = list(plan.agent_ifaces.keys())
    dispatch_started = time.monotonic()
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
                        writer_epoch=writer_epoch,
                    )
                    for addr in agent_addrs
                ]
            )
        )
    dispatch_elapsed_ms = (time.monotonic() - dispatch_started) * 1000.0

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
    if agent_addrs:
        _log_actuation_latency(
            operation="BatchLinkDown",
            pair_count=len(sorted_pairs),
            agent_count=len(agent_addrs),
            elapsed_ms=dispatch_elapsed_ms,
            result=result,
        )

    for pair in sorted(result.succeeded_pairs):
        info = actual_links.get(pair)
        if not info or info.contact is not None:
            continue
        await publish_link_down(
            js=js,
            subject=subj_link_down,
            pair=pair,
            info=info,
            sim_time=sim_time,
            reason=down_reasons.get(pair, "vis_lost"),
            losses=end_loss_records(
                result,
                pair=pair,
                info=info,
                cause="scenario" if pair in down_reasons else "end_of_contact",
            ),
        )
    return result


async def _dispatch_link_up_plan(
    *,
    plan: LinkUpBatchPlan,
    pairs: list[LinkPair],
    desired: Mapping[LinkPair, ActiveLinkInfo],
    pool: Any,
    sim_iso: str,
    gs_capacities: Mapping[str, int],
    session_id: str,
    wiring_generation: str,
    writer_epoch: int,
    label: str,
) -> ActuationResult:
    """Send one BatchLinkUp plan to its Node Agents and judge every pair on the acks."""
    agent_results: list[AgentCommandResult] = []
    agent_addrs = list(plan.agent_ifaces.keys())
    dispatch_started = time.monotonic()
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
                        writer_epoch=writer_epoch,
                        label=label,
                    )
                    for addr in agent_addrs
                ]
            )
        )
    dispatch_elapsed_ms = (time.monotonic() - dispatch_started) * 1000.0

    pair_link_type = {pair: desired[pair].link_type for pair in pairs}
    pair_gs_id = {
        pair: _gs_id_for_pair(pair, gs_capacities, pair_link_type[pair]) for pair in pairs
    }
    result = build_actuation_result(
        operation="BatchLinkUp",
        requested_pairs=pairs,
        pair_agent_ifaces=plan.pair_agent_ifaces,
        pair_link_type=pair_link_type,
        pair_gs_id=pair_gs_id,
        agent_results=agent_results,
    )
    if agent_addrs:
        _log_actuation_latency(
            operation="BatchLinkUp",
            pair_count=len(pairs),
            agent_count=len(agent_addrs),
            elapsed_ms=dispatch_elapsed_ms,
            result=result,
        )
    return result


async def _publish_link_ups(
    *,
    result: ActuationResult,
    plan: LinkUpBatchPlan,
    desired: Mapping[LinkPair, ActiveLinkInfo],
    js: Any,
    subj_link_up: str,
    sim_time: datetime,
    validate_authority_freshness: AuthorityFreshnessValidator,
    link_provenance: LinkProvenanceBuilder,
) -> None:
    """Publish LinkUp for every pair the Node Agents proved open."""
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
            latency_a_to_b_ms=info.latency_a_to_b_ms,
            latency_b_to_a_ms=info.latency_b_to_a_ms,
            range_km=info.range_km,
            reason="vis_gained",
            link_type=info.link_type,
            provenance=link_provenance(info, plan.pair_compensation[pair], sim_time),
            contact_id=info.contact.contact_id if info.contact is not None else None,
        )
        await js.publish(subj_link_up, event.model_dump_json().encode())


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
    interface_rates: Mapping[tuple[str, str], InterfaceRates],
    session_id: str,
    wiring_generation: str,
    writer_epoch: int,
) -> ActuationResult:
    """Open local links in one stage (OPEN) and publish LinkUp for each one proven."""
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
        interface_rates=interface_rates,
        stage=node_agent_pb2.LINK_UP_STAGE_OPEN,
    )
    result = await _dispatch_link_up_plan(
        plan=plan,
        pairs=sorted_pairs,
        desired=desired,
        pool=pool,
        sim_iso=sim_iso,
        gs_capacities=gs_capacities,
        session_id=session_id,
        wiring_generation=wiring_generation,
        writer_epoch=writer_epoch,
        label="up",
    )
    await _publish_link_ups(
        result=result,
        plan=plan,
        desired=desired,
        js=js,
        subj_link_up=subj_link_up,
        sim_time=sim_time,
        validate_authority_freshness=validate_authority_freshness,
        link_provenance=link_provenance,
    )
    return result


async def prepare_long_delay_contacts(
    *,
    pairs: set[LinkPair],
    desired: Mapping[LinkPair, ActiveLinkInfo],
    locator: Any,
    pool: Any,
    sim_iso: str,
    sim_time: datetime,
    gs_capacities: Mapping[str, int],
    latency_compensation: LatencyCompensationFn,
    validate_authority_freshness: AuthorityFreshnessValidator,
    interface_rates: Mapping[tuple[str, str], InterfaceRates],
    session_id: str,
    wiring_generation: str,
    writer_epoch: int,
) -> tuple[LinkUpBatchPlan, ActuationResult]:
    """PREPARE long-delay contacts at both ends: each end builds its shaping, receive queue and
    neighbours with admission stopped, so nothing crosses the link yet. Publishes nothing; the
    plan returned is the one ``open_prepared_contacts`` admits."""
    sorted_pairs = sorted(pairs)
    for pair in sorted_pairs:
        info = desired.get(pair)
        if info is None or info.contact is None:
            raise RuntimeError(f"PREPARE requested for {pair}, which is no desired contact")
        validate_authority_freshness(pair, info, sim_time, operation="LinkUp")
    plan = build_link_up_batch_plan(
        pairs=sorted_pairs,
        desired=desired,
        locator=locator,
        gs_capacities=gs_capacities,
        compensation_for_pair=latency_compensation,
        interface_rates=interface_rates,
        stage=node_agent_pb2.LINK_UP_STAGE_PREPARE,
    )
    result = await _dispatch_link_up_plan(
        plan=plan,
        pairs=sorted_pairs,
        desired=desired,
        pool=pool,
        sim_iso=sim_iso,
        gs_capacities=gs_capacities,
        session_id=session_id,
        wiring_generation=wiring_generation,
        writer_epoch=writer_epoch,
        label="prepare",
    )
    return plan, result


async def open_prepared_contacts(
    *,
    plan: LinkUpBatchPlan,
    pairs: set[LinkPair],
    desired: Mapping[LinkPair, ActiveLinkInfo],
    pool: Any,
    js: Any,
    subj_link_up: str,
    sim_iso: str,
    sim_time: datetime,
    gs_capacities: Mapping[str, int],
    validate_authority_freshness: AuthorityFreshnessValidator,
    link_provenance: LinkProvenanceBuilder,
    session_id: str,
    wiring_generation: str,
    writer_epoch: int,
) -> ActuationResult:
    """OPEN contacts both of whose ends PREPARE proved, with the prepared entries, and publish
    LinkUp for each contact whose two ends acknowledged the open."""
    sorted_pairs = sorted(pairs)
    open_plan = restage_link_up_plan(
        plan, pairs=sorted_pairs, stage=node_agent_pb2.LINK_UP_STAGE_OPEN
    )
    result = await _dispatch_link_up_plan(
        plan=open_plan,
        pairs=sorted_pairs,
        desired=desired,
        pool=pool,
        sim_iso=sim_iso,
        gs_capacities=gs_capacities,
        session_id=session_id,
        wiring_generation=wiring_generation,
        writer_epoch=writer_epoch,
        label="open",
    )
    await _publish_link_ups(
        result=result,
        plan=open_plan,
        desired=desired,
        js=js,
        subj_link_up=subj_link_up,
        sim_time=sim_time,
        validate_authority_freshness=validate_authority_freshness,
        link_provenance=link_provenance,
    )
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
    interface_rates: Mapping[tuple[str, str], InterfaceRates],
    session_id: str,
    wiring_generation: str,
    writer_epoch: int,
) -> ActuationResult:
    """Apply OME-authoritative latency changes for already-active links.

    Each entry carries its interface's own transmit rate: the Node Agent sizes
    the netem queue limit from rate and delay.
    """
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
        compensation = latency_compensation(node_a, node_b, info.latencies_ms)
        pair_compensation[pair] = compensation
        # Each end's egress carries the direction it transmits.
        netem = (compensation.netem_a_to_b_ms, compensation.netem_b_to_a_ms)
        # Remember what was COMMANDED: kernel proofs assert against these values.
        info.commanded_netem_ms = netem

        if info.link_type == "ground":
            gs_id, sat_id, gs_iface, sat_iface = required_ground_endpoints(
                pair, info, gs_capacities
            )
            locality = locator.link_locality(node_a, node_b)
            endpoint_agents = [(sat_id, sat_iface, locator.agent_addr(sat_id))]
            if locality == node_agent_pb2.LOCALITY_LOCAL:
                endpoint_agents.append((gs_id, gs_iface, locator.agent_addr(sat_id)))
            else:
                endpoint_agents.append((gs_id, gs_iface, locator.agent_addr(gs_id)))
            for endpoint_id, endpoint_iface, agent in endpoint_agents:
                entry = node_agent_pb2.LatencyEntry(
                    node_id=endpoint_id,
                    interface_name=endpoint_iface,
                    latency_ms=egress_ms(pair, endpoint_id, netem),
                    rates=terminal_rates_message(interface_rates, endpoint_id, endpoint_iface),
                    link_type=node_agent_pb2.LINK_TYPE_GROUND,
                    gs_id=gs_id,
                    sat_id=sat_id,
                )
                if info.contact is not None:
                    # A long-delay ground end's delay queue holds the direction its peer
                    # transmits, as on a long-delay fixed link.
                    peer_id = sat_id if endpoint_id == gs_id else gs_id
                    peer_iface = sat_iface if endpoint_id == gs_id else gs_iface
                    entry.contact_id = info.contact.contact_id
                    entry.receive_latency_ms = egress_ms(pair, peer_id, netem)
                    # The peer's transmit rate sizes that queue.
                    entry.peer_rates.CopyFrom(
                        terminal_rates_message(interface_rates, peer_id, peer_iface)
                    )
                _add_entry(pair, agent, entry)
        else:
            for nid, ifname, peer_nid, peer_ifname in [
                (node_a, info.interface_a, node_b, info.interface_b),
                (node_b, info.interface_b, node_a, info.interface_a),
            ]:
                entry = node_agent_pb2.LatencyEntry(
                    node_id=nid,
                    interface_name=ifname,
                    latency_ms=egress_ms(pair, nid, netem),
                    rates=terminal_rates_message(interface_rates, nid, ifname),
                    link_type=node_agent_pb2.LINK_TYPE_ISL,
                )
                if info.contact is not None:
                    # A long-delay end's delay queue holds the direction its peer transmits,
                    # and the peer's transmit rate sizes it.
                    entry.contact_id = info.contact.contact_id
                    entry.receive_latency_ms = egress_ms(pair, peer_nid, netem)
                    entry.peer_rates.CopyFrom(
                        terminal_rates_message(interface_rates, peer_nid, peer_ifname)
                    )
                _add_entry(pair, locator.agent_addr(nid), entry)

    agent_results: list[AgentCommandResult] = []
    dispatch_started = time.monotonic()
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
                        writer_epoch=writer_epoch,
                    )
                    for agent_addr in agent_entries
                ]
            )
        )
    dispatch_elapsed_ms = (time.monotonic() - dispatch_started) * 1000.0
    result = build_actuation_result(
        operation="SetLatency",
        requested_pairs=sorted_pairs,
        pair_agent_ifaces=pair_agent_ifaces,
        pair_link_type=pair_link_type,
        pair_gs_id=pair_gs_id,
        agent_results=agent_results,
    )
    if agent_entries:
        _log_actuation_latency(
            operation="SetLatency",
            pair_count=len(sorted_pairs),
            agent_count=len(agent_entries),
            elapsed_ms=dispatch_elapsed_ms,
            result=result,
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
            latency_a_to_b_ms=info.latency_a_to_b_ms,
            latency_b_to_a_ms=info.latency_b_to_a_ms,
            range_km=info.range_km,
            provenance=link_provenance(info, pair_compensation[pair], sim_time),
        )
        await js.publish(subj_latency, event.model_dump_json().encode())
    return result
