# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The order of a long-delay contact's commands: open, stop admission, close each end.

A long-delay link (a fixed link the resolver classes long-delay) drains at the end of its
contact. Both transmitters stop admitting at once (STOP_ADMISSION), and every packet already
emitted keeps travelling. Each end then closes on its own (CLOSE), when the last packet sent
toward it has arrived: the proven stop plus the light time of the direction it receives. The
Scheduler keeps the contact in its actual link record from the stop until the last end closes,
and records the teardown only then.

The close time is a time on the Scheduler's monotonic clock. Rejected alternative: closing when
OME's sim time passes the end of the contact plus the light time. netem releases each packet at
its enqueue time plus the delay, on the kernel's monotonic clock, and a delay change does not
retime packets already queued; at 100 times speed a 1,200 s drain spans 12 s of wall time, so a
sim-time close would destroy packets still in flight. OME's predicted reception window and the
kernel queue's drain are different facts.

The light time, not the commanded netem delay, sets the close. On a link between servers the
commanded delay is the light time less the measured substrate latency, and a close after the
netem delay alone would cut the last packets by that latency.

A seek or a scenario command ends a contact at once: its ends close without waiting, and the
packets still in flight are discarded and counted with that cause.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from scheduler.desired_state import ActiveLinkInfo, Drain, DrainingEnd

LinkPair = tuple[str, str]
ForcedCause = Literal["seek", "scenario"]


@dataclass(frozen=True)
class LongDelayPlan:
    """What one reconcile does to the long-delay contacts.

    ``forced``: contacts that end at once, with the cause; every end still open or draining
    closes now. ``stop``: open contacts that ended; both transmitters stop admitting.
    ``open``: desired contacts to open, including one that reopens a draining or renamed
    contact on the same pair. ``latency``: open contacts whose delays changed.
    """

    forced: Mapping[LinkPair, ForcedCause]
    stop: frozenset[LinkPair]
    open: frozenset[LinkPair]
    latency: frozenset[LinkPair]

    @property
    def empty(self) -> bool:
        return not (self.forced or self.stop or self.open or self.latency)


def plan_long_delay_contacts(
    actual: Mapping[LinkPair, ActiveLinkInfo],
    desired: Mapping[LinkPair, ActiveLinkInfo],
    *,
    epoch_id: int,
    down_reasons: Mapping[LinkPair, str],
    latency_tolerance_ms: float = 1e-9,
) -> LongDelayPlan:
    """Plan the long-delay contacts of one reconcile. ``actual`` and ``desired`` hold the
    long-delay pairs only; every entry carries a contact."""
    forced: dict[LinkPair, ForcedCause] = {}
    stop: set[LinkPair] = set()
    opening: set[LinkPair] = set()
    latency: set[LinkPair] = set()
    for pair in sorted(set(actual) | set(desired)):
        held = actual.get(pair)
        wanted = desired.get(pair)
        for info in (held, wanted):
            if info is not None and info.contact is None:
                raise ValueError(f"long-delay link {pair} has no contact in the link record")
        if held is not None and held.contact.epoch_id < epoch_id:  # type: ignore[union-attr]
            # A seek ended every contact of the old epoch, draining ones included.
            forced[pair] = "seek"
            if wanted is not None:
                opening.add(pair)
            continue
        if wanted is None:
            if held is None:
                continue
            if pair in down_reasons:
                forced[pair] = "scenario"
            elif held.drain is None:
                stop.add(pair)
            continue
        if held is None or held.drain is not None or held.contact != wanted.contact:
            opening.add(pair)
        elif (
            abs(held.latency_a_to_b_ms - wanted.latency_a_to_b_ms) > latency_tolerance_ms
            or abs(held.latency_b_to_a_ms - wanted.latency_b_to_a_ms) > latency_tolerance_ms
            or held.range_km != wanted.range_km
        ):
            latency.add(pair)
    return LongDelayPlan(
        forced=forced,
        stop=frozenset(stop),
        open=frozenset(opening),
        latency=frozenset(latency),
    )


def start_drain(
    pair: LinkPair,
    info: ActiveLinkInfo,
    *,
    stopped_mono: float,
    stopped_wall: datetime,
    reason: str,
) -> Drain:
    """The drain of a contact whose transmitters both proved admission stopped at
    ``stopped_mono``. node_a receives the b_to_a direction, node_b the a_to_b direction."""
    ends = []
    for node_id, interface, receive_ms in (
        (pair[0], info.interface_a, info.latency_b_to_a_ms),
        (pair[1], info.interface_b, info.latency_a_to_b_ms),
    ):
        ends.append(
            DrainingEnd(
                node_id=node_id,
                interface=interface,
                close_due=stopped_mono + receive_ms / 1000.0,
                receives_until=stopped_wall + timedelta(milliseconds=receive_ms),
            )
        )
    return Drain(stopped_at=stopped_wall, reason=reason, ends=tuple(ends))


def due_ends(
    actual: Mapping[LinkPair, ActiveLinkInfo], *, now_mono: float
) -> dict[LinkPair, frozenset[str]]:
    """The draining ends whose last packet has arrived by ``now_mono``, by pair."""
    due: dict[LinkPair, frozenset[str]] = {}
    for pair, info in sorted(actual.items()):
        if info.drain is None:
            continue
        ready = frozenset(end.node_id for end in info.drain.ends if end.close_due <= now_mono)
        if ready:
            due[pair] = ready
    return due


def next_close_due(actual: Mapping[LinkPair, ActiveLinkInfo]) -> float | None:
    """The earliest close of any draining end, on the Scheduler's monotonic clock."""
    return min(
        (end.close_due for info in actual.values() if info.drain for end in info.drain.ends),
        default=None,
    )


def remaining_ends(pair: LinkPair, info: ActiveLinkInfo) -> frozenset[str]:
    """The ends of a contact not yet closed: both while open, the draining ones while draining."""
    if info.drain is None:
        return frozenset(pair)
    return frozenset(end.node_id for end in info.drain.ends)
