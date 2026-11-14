# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Scheduler desired-state construction helpers.

This module is the Scheduler-side OME authority boundary. It preserves
OME-provided range and one-way latency, derives configured interfaces, and
fails loudly when required authority is missing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from nodalarc.models.events import VisibilityEvent
from nodalarc.models.link_events import LinkEndLoss
from nodalarc.models.link_state import AdminState, CarrierState, LinkState, contact_id


@dataclass(frozen=True, slots=True)
class Contact:
    """One contact of a fixed long-delay link, as OME names it: the epoch of the authority that
    opened it and the sim time it opened."""

    epoch_id: int
    opened_at: datetime

    @property
    def contact_id(self) -> str:
        return contact_id(self.epoch_id, self.opened_at)

    @classmethod
    def from_contact_id(cls, text: str) -> Contact:
        """The contact a Node Agent reports an end carries, from its name. A name that is not
        one ``contact_id`` makes is refused."""
        epoch, separator, opened = text.partition("@")
        if not separator or not epoch.isdigit() or not opened.endswith("Z"):
            raise ValueError(f"{text!r} is not a long-delay contact name")
        contact = cls(
            epoch_id=int(epoch),
            opened_at=datetime.strptime(opened, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC),
        )
        if contact.contact_id != text:
            raise ValueError(f"{text!r} is not a long-delay contact name")
        return contact


@dataclass(frozen=True, slots=True)
class DrainingEnd:
    """One end of a draining contact that still receives. It closes at ``close_due`` on the
    Scheduler's monotonic clock: the proven stop plus the light time of the direction it
    receives. ``receives_until`` is the same instant on the wall clock, for observation.
    ``contact_id`` is the contact the end's kernel carries, the one its close names: the link
    record's contact, except at an end a restarted Scheduler found carrying another."""

    node_id: str
    interface: str
    close_due: float
    receives_until: datetime
    contact_id: str


@dataclass(frozen=True, slots=True)
class Drain:
    """A long-delay contact whose transmitters both stopped admitting.

    ``ends`` are the ends still receiving; ``closed`` holds what each closed end lost. The
    contact's teardown is recorded when the last end closes. ``reason`` is why the contact
    ended (the LinkDown reason)."""

    stopped_at: datetime
    reason: str
    ends: tuple[DrainingEnd, ...]
    closed: tuple[LinkEndLoss, ...] = ()


@dataclass(frozen=True, slots=True)
class StageLoss:
    """What one long-delay end reported at its contact's OPEN (refused while the contact was
    prepared) and STOP (purged before transmission, and its transmit queue's drops until then).
    The Node Agent reports each count once, since its last report."""

    refused_before_open: int = 0
    untransmitted_at_stop: int = 0
    transmit_dropped: int = 0

    def plus(self, other: StageLoss) -> StageLoss:
        return StageLoss(
            refused_before_open=self.refused_before_open + other.refused_before_open,
            untransmitted_at_stop=self.untransmitted_at_stop + other.untransmitted_at_stop,
            transmit_dropped=self.transmit_dropped + other.transmit_dropped,
        )


class ActiveLinkInfo:
    """Mutable internal state for a desired or active link.

    Delays are per direction in the pair's order (node_a, node_b): ``a_to_b`` is the direction
    node_a transmits, carried on node_a's egress; ``b_to_a`` the direction node_b transmits.

    TODO(trust-gap-closure#10): Make this a frozen dataclass. Mutability
    exists only because latency updates on active links currently assign
    the commanded delays in place. Replace with dataclasses.replace() on a frozen
    type. This eliminates the mutable-value-in-frozen-intent
    contract gap where DispatchIntent is frozen but its dict values are not.
    """

    __slots__ = (
        "interface_a",
        "interface_b",
        "latency_a_to_b_ms",
        "latency_b_to_a_ms",
        "commanded_netem_ms",
        "assigned_terminals",
        "range_km",
        "authority_sim_time",
        "authority_source",
        "authority_sequence",
        "contact",
        "drain",
        "stage_losses",
    )

    @property
    def latencies_ms(self) -> tuple[float, float]:
        return (self.latency_a_to_b_ms, self.latency_b_to_a_ms)

    def __init__(
        self,
        interface_a: str,
        interface_b: str,
        latency_a_to_b_ms: float,
        latency_b_to_a_ms: float,
        *,
        assigned_terminals: bool,
        range_km: float | None = None,
        authority_sim_time: datetime | None = None,
        authority_source: str | None = None,
        authority_sequence: int | None = None,
        commanded_netem_ms: tuple[float, float] | None = None,
        contact: Contact | None = None,
    ) -> None:
        self.interface_a = interface_a
        self.interface_b = interface_b
        self.latency_a_to_b_ms = latency_a_to_b_ms
        self.latency_b_to_a_ms = latency_b_to_a_ms
        # The netem delays actually COMMANDED to the Node Agent for this link,
        # (a_to_b, b_to_a): orbital latency minus the substrate compensation
        # measured at dispatch time. Set by the command builders when a LinkUp or
        # SetLatency is sent; kernel proofs assert against these values, never
        # against a live recomputation - compensation inputs drift between
        # dispatch and proof, and that drift is not kernel divergence.
        self.commanded_netem_ms = commanded_netem_ms
        # Whether OME assigned the link's terminals when it scheduled the link. False for a
        # link on interfaces the resolver fixed. The Node Agent has one actuation path for
        # each kind.
        self.assigned_terminals = assigned_terminals
        self.range_km = range_km
        self.authority_sim_time = authority_sim_time
        self.authority_source = authority_source
        self.authority_sequence = authority_sequence
        # The contact on a fixed long-delay link (OME names it); None on a local link.
        self.contact = contact
        # Set on an actual long-delay link once both transmitters proved admission stopped;
        # cleared when the link record goes (last end closed) or the pair reopens.
        self.drain: Drain | None = None
        # By end node id: what a long-delay end reported losing at this contact's OPEN and STOP,
        # kept until the end's close or reopen completes its record.
        self.stage_losses: dict[str, StageLoss] = {}

    @property
    def long_delay(self) -> bool:
        return self.contact is not None

    def end_contact_id(self, node_id: str) -> str:
        """The contact an end of this long-delay link carries: its draining end's, else the
        record's."""
        if self.contact is None:
            raise ValueError("a local link carries no contact")
        if self.drain is not None:
            for end in self.drain.ends:
                if end.node_id == node_id:
                    return end.contact_id
        return self.contact.contact_id


def require_ome_geometry(
    pair: tuple[str, str],
    *,
    range_km: float | None,
    latency_a_to_b_ms: float | None,
    latency_b_to_a_ms: float | None,
    source: str,
) -> tuple[float, float, float]:
    """Require OME-authoritative range and each direction's one-way latency.

    The Scheduler is not a physics authority. Missing range/latency is a
    corrupt control-plane input, not a condition to paper over with a fallback.
    Raising here stops dispatch before the emulator can apply made-up physics.
    """
    if range_km is None:
        raise ValueError(f"{source} for {pair} is missing OME-authoritative range_km")
    if range_km < 0:
        raise ValueError(f"{source} for {pair} has negative range_km={range_km}")
    if latency_a_to_b_ms is None:
        raise ValueError(f"{source} for {pair} is missing OME-authoritative latency_a_to_b_ms")
    if latency_b_to_a_ms is None:
        raise ValueError(f"{source} for {pair} is missing OME-authoritative latency_b_to_a_ms")
    if latency_a_to_b_ms < 0 or latency_b_to_a_ms < 0:
        raise ValueError(
            f"{source} for {pair} has a negative latency: a_to_b={latency_a_to_b_ms}, "
            f"b_to_a={latency_b_to_a_ms}"
        )
    return range_km, latency_a_to_b_ms, latency_b_to_a_ms


def _contact(
    pair: tuple[str, str],
    *,
    opened_at: datetime | None,
    long_delay_pairs: frozenset[tuple[str, str]],
    epoch_id: int,
    source: str,
) -> Contact | None:
    """The contact of an up link: OME names one on every fixed long-delay link and on no other.
    A disagreement between OME and the resolver's class stops dispatch."""
    if pair in long_delay_pairs:
        if opened_at is None:
            raise ValueError(
                f"{source} for long-delay link {pair} names no contact; the Scheduler cannot "
                "target its queues"
            )
        return Contact(epoch_id=epoch_id, opened_at=opened_at)
    if opened_at is not None:
        raise ValueError(
            f"{source} names a contact on {pair}, which the resolved session classes local"
        )
    return None


_SOURCE_LABEL = {
    "visibility_event": "VisibilityEvent",
    "link_state_snapshot": "LinkStateSnapshot",
}


def link_interfaces(
    pair: tuple[str, str],
    *,
    terminal_index_a: int | None,
    terminal_index_b: int | None,
    interface_map: Mapping[tuple[str, str], tuple[str, str]],
    terminal_interface_names: Mapping[str, Mapping[int, str]],
    source: str,
) -> tuple[str, str]:
    """The pod interface at each end of an up link, in pair order.

    OME names the terminal it assigned a link at each node by index, and the interface is
    the one the resolved session gives that node's index. A link whose interfaces the
    resolver fixed carries no index and takes them from the resolved session's fixed map.
    No name is built from a pattern here: the resolver owns interface names. Rejected
    alternative: build ``term{n}`` and ``gnd{n}`` from the index and a ground-station test,
    which tied the Scheduler to one ground station and one satellite per link.
    """
    if (terminal_index_a is None) != (terminal_index_b is None):
        raise ValueError(
            f"{source} for {pair} names a terminal at one end only: "
            f"terminal_index_a={terminal_index_a!r}, terminal_index_b={terminal_index_b!r}"
        )
    if terminal_index_a is None or terminal_index_b is None:
        fixed = interface_map.get(pair)
        if fixed is None:
            raise ValueError(
                f"{source} for {pair} names no terminals and the resolved session fixes no "
                "interfaces for the pair; refusing to dispatch an unmapped link"
            )
        return fixed
    names: list[str] = []
    for node_id, index in zip(pair, (terminal_index_a, terminal_index_b), strict=True):
        name = terminal_interface_names.get(node_id, {}).get(index)
        if name is None:
            raise ValueError(
                f"{source} for {pair} names terminal {index} at {node_id}, which the "
                "resolved session does not give that node"
            )
        names.append(name)
    return names[0], names[1]


def snapshot_row_is_up(link: LinkState, *, snapshot_sim_time: datetime) -> bool:
    """Whether a row of a LinkStateSnapshot states its link up. An up row timed apart from
    its snapshot is a corrupt snapshot and stops dispatch."""
    if link.admin != AdminState.UP or link.carrier != CarrierState.UP:
        return False
    if link.sim_time != snapshot_sim_time:
        raise ValueError(
            "LinkStateSnapshot contains a link whose sim_time does not match "
            f"the snapshot authority time: link={link.sim_time.isoformat()} "
            f"snapshot={snapshot_sim_time.isoformat()}"
        )
    return True


def desired_link(
    source: VisibilityEvent | LinkState,
    *,
    interface_map: Mapping[tuple[str, str], tuple[str, str]],
    terminal_interface_names: Mapping[str, Mapping[int, str]],
    long_delay_pairs: frozenset[tuple[str, str]],
    epoch_id: int,
    authority_sim_time: datetime,
    authority_source: Literal["visibility_event", "link_state_snapshot"],
    authority_sequence: int | None = None,
) -> tuple[tuple[str, str], ActiveLinkInfo]:
    """Build one desired link from OME's statement that the link is up.

    The statement is a scheduled visible VisibilityEvent or an up row of a
    LinkStateSnapshot. Both carry the link's nodes, range, delays, terminals and contact
    under the same names, so one builder serves both. The caller says which authority it
    is, and at what sim time and sequence.
    """
    label = _SOURCE_LABEL[authority_source]
    pair = (source.node_a, source.node_b)
    range_km, a_to_b, b_to_a = require_ome_geometry(
        pair,
        range_km=source.range_km,
        latency_a_to_b_ms=source.latency_a_to_b_ms,
        latency_b_to_a_ms=source.latency_b_to_a_ms,
        source=label,
    )
    interface_a, interface_b = link_interfaces(
        pair,
        terminal_index_a=source.terminal_index_a,
        terminal_index_b=source.terminal_index_b,
        interface_map=interface_map,
        terminal_interface_names=terminal_interface_names,
        source=label,
    )
    return pair, ActiveLinkInfo(
        interface_a=interface_a,
        interface_b=interface_b,
        latency_a_to_b_ms=a_to_b,
        latency_b_to_a_ms=b_to_a,
        assigned_terminals=source.terminal_index_a is not None,
        range_km=range_km,
        authority_sim_time=authority_sim_time,
        authority_source=authority_source,
        authority_sequence=authority_sequence,
        contact=_contact(
            pair,
            opened_at=source.contact_opened_at,
            long_delay_pairs=long_delay_pairs,
            epoch_id=epoch_id,
            source=label,
        ),
    )
