# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""What a restarted Scheduler adopts of the long-delay contacts in the kernel.

A Scheduler that restarts finds long-delay ends its predecessor opened, stopped or left in the
middle of a stage. Before it publishes its first actual-links baseline it surveys both ends of
every long-delay link through KernelInventory and adopts what they carry:

- both ends carry the same contact with admission open: an open contact, adopted as it stands;
  the first reconcile against OME retimes, stops or reopens it like any open contact;
- any other carried state (a contact draining, an interrupted stop, open or prepare, one end
  already closed, the two ends on different contacts): a recovered contact. Every end that
  carries a contact is stopped, which finishes an interrupted stop and purges and counts what
  waited to be transmitted, and then drains: it closes after the resolver's bound on the link's
  one-way delay, counted from the final stop acknowledgement;
- neither end carries a contact: nothing to adopt.

Rejected alternative: re-preparing every contact OME still names. PREPARE stops admission on an
open contact and refuses packets until OPEN, so an adopted open contact would lose traffic to
the restart alone. The kernel's counters are never reset: every count the Node Agent reports is
what it counted since its last report, so adoption reports nothing twice.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from scheduler.actuation import EndSurvey
from scheduler.desired_state import ActiveLinkInfo, Contact

LinkPair = tuple[str, str]


@dataclass(frozen=True)
class AdoptedOpen:
    """Both ends carry ``contact`` with admission open. ``receive_ms`` is each end's receive
    queue delay by node id: what the earlier Scheduler commanded for the direction it receives."""

    contact: Contact
    receive_ms: Mapping[str, float]


@dataclass(frozen=True)
class RecoveredContact:
    """A contact in any other state. ``carried`` maps each end that carries a contact to that
    contact's name; ``contact`` is the oldest of them, the one that ended."""

    contact: Contact
    carried: Mapping[str, str]
    receive_ms: Mapping[str, float]


def classify_surveyed_pair(
    pair: LinkPair, surveys: Mapping[str, EndSurvey]
) -> AdoptedOpen | RecoveredContact | None:
    """What a restarted Scheduler adopts of ``pair``, from the survey of each end (by node id)."""
    missing = sorted(set(pair) - set(surveys))
    if missing:
        raise ValueError(f"long-delay link {pair} has no survey of {missing}")
    carried = {
        node: surveys[node].carried_contact_id for node in pair if surveys[node].carried_contact_id
    }
    if not carried:
        return None
    receive_ms = {node: surveys[node].receive_latency_ms for node in carried}
    names = set(carried.values())
    if (
        len(carried) == 2
        and len(names) == 1
        and not any(surveys[node].admission_stopped for node in pair)
    ):
        return AdoptedOpen(contact=Contact.from_contact_id(names.pop()), receive_ms=receive_ms)
    contacts = sorted(
        (Contact.from_contact_id(name) for name in names),
        key=lambda contact: (contact.epoch_id, contact.opened_at),
    )
    return RecoveredContact(contact=contacts[0], carried=carried, receive_ms=receive_ms)


def adopted_link_info(
    pair: LinkPair, interfaces: tuple[str, str], found: AdoptedOpen | RecoveredContact
) -> ActiveLinkInfo:
    """The link record of an adopted contact. The orbital delays are OME's and unknown until its
    next snapshot; the record holds the delays the kernel queues carry, which the first
    reconcile retimes to OME's. An end that carries no contact has no queue delay (0)."""
    # node_b's queue delays what node_a transmits (a_to_b), node_a's what node_b transmits.
    a_to_b = found.receive_ms.get(pair[1], 0.0)
    b_to_a = found.receive_ms.get(pair[0], 0.0)
    return ActiveLinkInfo(
        interface_a=interfaces[0],
        interface_b=interfaces[1],
        latency_a_to_b_ms=a_to_b,
        latency_b_to_a_ms=b_to_a,
        link_type="isl",
        commanded_netem_ms=(a_to_b, b_to_a),
        contact=found.contact,
    )
