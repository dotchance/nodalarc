# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Scheduler desired-state construction helpers.

This module is the Scheduler-side OME authority boundary. It preserves
OME-provided range and one-way latency, derives configured interfaces, and
fails loudly when required authority is missing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

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


@dataclass(frozen=True, slots=True)
class DrainingEnd:
    """One end of a draining contact that still receives. It closes at ``close_due`` on the
    Scheduler's monotonic clock: the proven stop plus the OME light time of the direction it
    receives. ``receives_until`` is the same instant on the wall clock, for observation."""

    node_id: str
    interface: str
    close_due: float
    receives_until: datetime


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
        "link_type",
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
        link_type: str,
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
        self.link_type = link_type
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


def _ground_interfaces(
    pair: tuple[str, str],
    *,
    ground_station_ids: frozenset[str],
    gs_terminal_index: int | None,
    sat_terminal_index: int | None,
) -> tuple[str, str]:
    if gs_terminal_index is None or sat_terminal_index is None:
        raise ValueError(
            "Scheduled ground links require OME-provided terminal indices; "
            f"got gs_terminal_index={gs_terminal_index!r}, "
            f"sat_terminal_index={sat_terminal_index!r}"
        )

    a_is_ground = pair[0] in ground_station_ids
    b_is_ground = pair[1] in ground_station_ids
    if a_is_ground == b_is_ground:
        role = "no ground endpoint" if not a_is_ground else "two ground endpoints"
        raise ValueError(
            f"Scheduled ground link {pair} has {role}; refusing to infer "
            "interface ownership without exactly one configured ground station"
        )

    gs_iface = f"term{gs_terminal_index}"
    sat_iface = f"gnd{sat_terminal_index}"
    if a_is_ground:
        return gs_iface, sat_iface
    return sat_iface, gs_iface


def desired_link_from_visibility(
    vis: VisibilityEvent,
    *,
    interface_map: dict[tuple[str, str], tuple[str, str]],
    ground_station_ids: frozenset[str],
    long_delay_pairs: frozenset[tuple[str, str]],
    epoch_id: int,
) -> tuple[tuple[str, str], ActiveLinkInfo]:
    """Build one desired link from a scheduled visible OME event."""
    pair = (vis.node_a, vis.node_b)
    range_km, a_to_b, b_to_a = require_ome_geometry(
        pair,
        range_km=vis.range_km,
        latency_a_to_b_ms=vis.latency_a_to_b_ms,
        latency_b_to_a_ms=vis.latency_b_to_a_ms,
        source="VisibilityEvent",
    )

    if vis.link_type == "ground":
        ifaces = _ground_interfaces(
            pair,
            ground_station_ids=ground_station_ids,
            gs_terminal_index=vis.gs_terminal_index,
            sat_terminal_index=vis.sat_terminal_index,
        )
    else:
        ifaces = interface_map.get(pair)
        if ifaces is None:
            raise ValueError(
                f"VisibilityEvent for {pair} has no configured ISL interfaces; "
                "refusing to dispatch an unmapped link"
            )

    return pair, ActiveLinkInfo(
        interface_a=ifaces[0],
        interface_b=ifaces[1],
        latency_a_to_b_ms=a_to_b,
        latency_b_to_a_ms=b_to_a,
        link_type=vis.link_type,
        range_km=range_km,
        authority_sim_time=vis.sim_time,
        authority_source="visibility_event",
        contact=_contact(
            pair,
            opened_at=vis.contact_opened_at,
            long_delay_pairs=long_delay_pairs,
            epoch_id=epoch_id,
            source="VisibilityEvent",
        ),
    )


def desired_link_from_snapshot_link(
    link: LinkState,
    *,
    interface_map: dict[tuple[str, str], tuple[str, str]],
    ground_station_ids: frozenset[str],
    snapshot_sim_time: datetime,
    snapshot_seq: int,
    long_delay_pairs: frozenset[tuple[str, str]],
    epoch_id: int,
) -> tuple[tuple[str, str], ActiveLinkInfo] | None:
    """Build one desired link from an authoritative snapshot link entry."""
    if link.admin != AdminState.UP or link.carrier != CarrierState.UP:
        return None
    if link.sim_time != snapshot_sim_time:
        raise ValueError(
            "LinkStateSnapshot contains a link whose sim_time does not match "
            f"the snapshot authority time: link={link.sim_time.isoformat()} "
            f"snapshot={snapshot_sim_time.isoformat()}"
        )

    pair = (link.node_a, link.node_b)
    range_km, a_to_b, b_to_a = require_ome_geometry(
        pair,
        range_km=link.range_km,
        latency_a_to_b_ms=link.latency_a_to_b_ms,
        latency_b_to_a_ms=link.latency_b_to_a_ms,
        source="LinkStateSnapshot",
    )

    if link.link_type == "ground":
        ifaces = _ground_interfaces(
            pair,
            ground_station_ids=ground_station_ids,
            gs_terminal_index=link.gs_terminal_index,
            sat_terminal_index=link.sat_terminal_index,
        )
    else:
        ifaces = interface_map.get(pair)
        if ifaces is None:
            raise ValueError(
                f"LinkStateSnapshot for {pair} has no configured ISL interfaces; "
                "refusing to dispatch an unmapped link"
            )

    return pair, ActiveLinkInfo(
        interface_a=ifaces[0],
        interface_b=ifaces[1],
        latency_a_to_b_ms=a_to_b,
        latency_b_to_a_ms=b_to_a,
        link_type=link.link_type,
        range_km=range_km,
        authority_sim_time=snapshot_sim_time,
        authority_source="link_state_snapshot",
        authority_sequence=snapshot_seq,
        contact=_contact(
            pair,
            opened_at=link.contact_opened_at,
            long_delay_pairs=long_delay_pairs,
            epoch_id=epoch_id,
            source="LinkStateSnapshot",
        ),
    )
