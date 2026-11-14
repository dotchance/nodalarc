# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""What a starting Scheduler takes over from the Scheduler that commanded before it.

The Scheduler that last commanded a session's wiring generation retains two kinds of record on
NODALARC_LINKS. Its ActualLinkSnapshot lists the links the Node Agents proved up, with the delays
it commanded on each. Each actuation record holds the state of one node that chooses links, its
recovery and its stale footprint. A Scheduler that holds the writer Lease reads both before it
commands anything, so its first reconcile starts from what the kernel carries: it takes down the
links OME no longer wants, brings up the new ones and retimes every link it took over.

The record describes the network namespaces of the pods the predecessor's wiring gate proved,
named by their sandboxes in the snapshot. A pod replaced since then (a ``make upgrade`` that
rolls the session images replaces every one) was wired anew by its Node Agent from nothing, under
the same session and wiring generation. What the record says about that pod is not in the
kernel, so the successor takes over only what sits in sandboxes its own wiring gate still
proves. A link with both ends on replaced pods is gone. A link with one end on a replaced pod
left its other half where it was: the half of a link a node chooses becomes that node's stale
footprint and the node starts unproven; a fixed link is left out, and the first reconcile brings it up
as a Scheduler with no predecessor does.

Rejected alternative: survey the kernel with KernelInventory and build ``_actual_links`` from what
it finds. KernelInventory is the referee that compares the kernel with the Scheduler's record. A
referee that writes the record it judges proves nothing, so the invariants forbid it. Rejected
too: fence the record by the session's run identity. The Operator keeps it when one pod is
replaced, so it cannot tell which namespaces still hold the record's links.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from nodalarc.models.events import OpsEvent
from nodalarc.models.scheduler_ops import (
    ActualLinkSnapshot,
    ActuationOpsDetails,
    ActuationState,
    CommandedLink,
    LinkFootprint,
    SchedulerOpsCode,
    current_actual_links,
)
from pydantic import BaseModel, ConfigDict, ValidationError

from scheduler.actuation import LinkPair, NodeActuationState
from scheduler.desired_state import ActiveLinkInfo

# The retained per-node records that carry a node's state; every other code is log only.
NODE_STATE_CODES = frozenset(
    {
        SchedulerOpsCode.ACTUATION_CLEAN,
        SchedulerOpsCode.ACTUATION_BLOCKED,
        SchedulerOpsCode.KERNEL_DIRTY,
    }
)


@dataclass(frozen=True)
class Takeover:
    """What a starting Scheduler took over from its predecessor.

    ``recorded``: the nodes whose retained state it resumes. ``rewired``: the nodes whose pod
    was replaced since the predecessor's wiring gate; their Node Agent wired them anew, and the
    successor's own wiring gate proved that wiring. ``unproven``: the nodes left holding a link
    half whose other end was rewired, which start kernel_dirty until KernelInventory proves the
    half down.
    """

    predecessor: str
    recorded: frozenset[str]
    rewired: frozenset[str]
    unproven: frozenset[str]


def rewired_nodes(
    snapshot: ActualLinkSnapshot, wired_sandboxes: Mapping[str, str]
) -> frozenset[str]:
    """The nodes whose pod sandbox differs from the one the predecessor's wiring gate proved."""
    return frozenset(
        node_id
        for node_id, sandbox in wired_sandboxes.items()
        if snapshot.wired_sandboxes.get(node_id) != sandbox
    )


def still_in_kernel(
    footprint: Mapping[LinkPair, ActiveLinkInfo], rewired: frozenset[str]
) -> dict[LinkPair, ActiveLinkInfo]:
    """The rows of a stale footprint that can still hold kernel state: those with an end on a pod
    that was not wired anew. A row with both ends rewired names nothing left in the kernel."""
    return {pair: info for pair, info in footprint.items() if not set(pair) <= rewired}


class _RecordIdentity(BaseModel):
    """The session and wiring generation a retained record belongs to, read before the record
    itself so a record of another generation is passed over whatever build wrote it."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    wiring_generation: str
    scheduler_instance_id: str


async def read_retained(js: Any, *, stream: str, subjects: str) -> list[bytes]:
    """The last message of every subject the filter matches, read from the stream directly: no
    consumer and no wait for a delivery. A message that expired between the listing and the read
    no longer exists and is left out."""
    from nats.js.errors import NotFoundError

    info = await js.stream_info(stream, subjects_filter=subjects)
    messages = []
    for subject in sorted(info.state.subjects or {}):
        try:
            message = await js.get_last_msg(stream, subject)
        except NotFoundError:
            continue
        messages.append(message.data)
    return messages


def _incompatible(record: str, identity: _RecordIdentity, exc: ValidationError) -> RuntimeError:
    return RuntimeError(
        f"the retained {record} of Scheduler {identity.scheduler_instance_id} for session "
        f"{identity.session_id} generation {identity.wiring_generation} does not match this "
        f"build ({exc.error_count()} errors: {exc.errors()[0]['msg']}); this Scheduler cannot "
        "know what the kernel carries. Deploy the session again."
    )


def predecessor_snapshot(
    messages: list[bytes], *, session_id: str, wiring_generation: str
) -> ActualLinkSnapshot | None:
    """The current ActualLinkSnapshot of this session's wiring generation, None when no Scheduler
    of the generation published one. A record of the generation that this build cannot read
    refuses the start."""
    snapshots = []
    for data in messages:
        identity = _RecordIdentity.model_validate_json(data)
        if (identity.session_id, identity.wiring_generation) != (session_id, wiring_generation):
            continue
        try:
            snapshots.append(ActualLinkSnapshot.model_validate_json(data))
        except ValidationError as exc:
            raise _incompatible("ActualLinkSnapshot", identity, exc) from exc
    return current_actual_links(
        snapshots, session_id=session_id, wiring_generation=wiring_generation
    )


def predecessor_node_records(
    messages: list[bytes], *, session_id: str, wiring_generation: str
) -> dict[str, tuple[SchedulerOpsCode, ActuationOpsDetails]]:
    """Each node's retained actuation record of this session's wiring generation, by node.
    A record of the generation that this build cannot read refuses the start."""
    records: dict[str, tuple[SchedulerOpsCode, ActuationOpsDetails]] = {}
    for data in messages:
        event = OpsEvent.model_validate_json(data)
        if event.details is None:
            raise RuntimeError(f"retained actuation record {event.code} carries no details")
        identity = _RecordIdentity.model_validate(event.details)
        if (identity.session_id, identity.wiring_generation) != (session_id, wiring_generation):
            continue
        try:
            details = ActuationOpsDetails.model_validate(event.details)
        except ValidationError as exc:
            raise _incompatible("actuation record", identity, exc) from exc
        code = SchedulerOpsCode(event.code)
        if details.node_id is None:
            # The details model ignores fields it does not know, so a record another build
            # wrote under another field name reads as a record of no node.
            raise RuntimeError(
                f"the retained actuation record {code} of Scheduler "
                f"{identity.scheduler_instance_id} for session {identity.session_id} generation "
                f"{identity.wiring_generation} names no node: another build wrote it, and this "
                "Scheduler cannot know what the kernel carries. Deploy the session again."
            )
        if code not in NODE_STATE_CODES:
            raise RuntimeError(
                f"retained actuation record {code} of {details.node_id} is not a node state"
            )
        records[details.node_id] = (code, details)
    return records


def inherited_actual_links(
    snapshot: ActualLinkSnapshot,
    *,
    interface_map: Mapping[LinkPair, tuple[str, str]],
    terminal_capacities: Mapping[str, int],
    long_delay_pairs: frozenset[LinkPair],
    rewired: frozenset[str],
) -> tuple[dict[LinkPair, ActiveLinkInfo], dict[LinkPair, ActiveLinkInfo]]:
    """The predecessor's proven links as this Scheduler's record of them, and the halves of
    links with assigned terminals that a rewired pod left at the other end.

    Each record holds the delays the kernel was commanded and no OME range: OME's geometry for
    the link is unknown until its next snapshot, so the first reconcile retimes every inherited
    link it still wants, as it retimes an adopted long-delay contact. A fixed link sits on the
    interfaces the session's map gives it, every other link on terminals OME assigned at two
    nodes that have such terminals; a link that fits neither is not this session's and refuses
    the start. Long-delay links are left out:
    their contacts live in the kernel, and the long-delay adoption surveys them there. A link
    with an end on a rewired pod is not in the kernel (see the module docstring); when only one
    end of a link with assigned terminals was rewired, the other half comes back as a stale
    footprint row.
    """
    links: dict[LinkPair, ActiveLinkInfo] = {}
    stale: dict[LinkPair, ActiveLinkInfo] = {}
    for row in snapshot.active_links:
        pair = (row.pair[0], row.pair[1])
        if pair in long_delay_pairs:
            continue
        interfaces = (row.interfaces[0], row.interfaces[1])
        if pair in interface_map:
            if interface_map[pair] != interfaces:
                raise RuntimeError(
                    f"Scheduler {snapshot.scheduler_instance_id} proved {pair} up on {interfaces}; "
                    f"the session wires it on {interface_map[pair]}"
                )
            link_type = "isl"
        elif all(node_id in terminal_capacities for node_id in pair):
            link_type = "ground"
        else:
            raise RuntimeError(
                f"Scheduler {snapshot.scheduler_instance_id} proved {pair} up; it is neither a "
                "fixed link of this session nor a link between two nodes with terminals OME "
                "assigns"
            )
        ends_rewired = len(set(pair) & rewired)
        if ends_rewired == 1 and link_type == "ground":
            stale[pair] = ActiveLinkInfo(interfaces[0], interfaces[1], 0.0, 0.0, link_type="ground")
        if ends_rewired:
            continue
        netem = (row.netem_ms[0], row.netem_ms[1])
        links[pair] = ActiveLinkInfo(
            interfaces[0],
            interfaces[1],
            netem[0],
            netem[1],
            link_type=link_type,
            commanded_netem_ms=netem,
        )
    return links, stale


def inherited_node_state(
    node_id: str, code: SchedulerOpsCode, details: ActuationOpsDetails
) -> tuple[NodeActuationState, dict[LinkPair, ActiveLinkInfo]]:
    """A node's state as its predecessor last recorded it, and its stale footprint.

    A clean node carries nothing more. A node that is not clean keeps its failure, its
    Node Agent results and its recovery (verify attempts, schedule, exhaustion), so the successor
    resumes the same proof. Its stale footprint comes back as link records that only name
    interfaces: KernelInventory proves them down and repair takes them down, and neither reads a
    delay, so they hold none.
    """
    state = details.actuation_state_after
    if state == ActuationState.UNKNOWN:
        raise RuntimeError(f"the retained record of {node_id} states no actuation state")
    if state == ActuationState.CLEAN:
        return NodeActuationState(node_id=node_id), {}
    stale = {
        (row.pair[0], row.pair[1]): ActiveLinkInfo(
            row.interfaces[0], row.interfaces[1], 0.0, 0.0, link_type="ground"
        )
        for row in details.stale_links
    }
    return (
        NodeActuationState(
            node_id=node_id,
            state=state,
            reason_code=code,
            affected_pairs=frozenset((a, b) for a, b in details.affected_pairs),
            node_agent_results=tuple(details.node_agent_results),
            recovery=details.recovery_status,
        ),
        stale,
    )


def commanded_rows(
    actual: Mapping[LinkPair, ActiveLinkInfo], pairs: set[LinkPair]
) -> list[CommandedLink]:
    """The proven links as the retained snapshot lists them. A proven link always carries the
    delays it was commanded with; one without them is a Scheduler fault."""
    rows = []
    for pair in sorted(pairs):
        info = actual[pair]
        if info.commanded_netem_ms is None:
            raise RuntimeError(f"proven link {pair} carries no commanded delay")
        rows.append(
            CommandedLink(
                pair=[pair[0], pair[1]],
                interfaces=[info.interface_a, info.interface_b],
                netem_ms=[info.commanded_netem_ms[0], info.commanded_netem_ms[1]],
            )
        )
    return rows


def footprint_rows(stale: Mapping[LinkPair, ActiveLinkInfo]) -> list[LinkFootprint]:
    """A node's stale footprint as its retained record lists it."""
    return [
        LinkFootprint(pair=[pair[0], pair[1]], interfaces=[info.interface_a, info.interface_b])
        for pair, info in sorted(stale.items())
    ]
