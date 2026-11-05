# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Admission on the transmitting terminal of a long-delay link.

At the end of a long-delay contact the transmitter stops admitting packets, and the packets it
already emitted keep travelling for the link's delay and arrive. Admission is a filter on the
egress side of a ``clsact`` qdisc on the transmitting pod interface, whose action consumes every
packet. The filter acts before the interface's queueing discipline, so a packet it takes never
enters the rate or delay queues, and the filter's action counts it.

The action consumes packets as ``stolen``, which tells the sender the packet went out. A
transmitter whose contact ended still radiates; nothing tells it no receiver listens. Rejected
alternative: the ``drop`` action, which reports the packet dropped. Measured on the cluster: a
sender on a packet socket failed with ENOBUFS on its first frame after the stop, and a TCP
sender reads a local drop as local congestion, which the physical link never signals.

Every long-delay terminal interface gets the ``clsact`` qdisc when it is created, before it
carries traffic, with admission stopped; its contact admits packets only at OPEN, once both ends
of the contact are prepared. Stopping or resuming admission changes only the filter; local links
carry no hook and keep their LinkDown unchanged. The filter acts before the rate shaper, so a
stop also purges what waits behind the shaper (``purge_untransmitted``). A stopped admission also freezes what enters a link end's
queues, which is what lets the loss at a queue's reset be counted exactly. Rejected alternative: adding ``clsact`` when
admission stops. Grafting a qdisc onto a device resets the device's queues; measured with a
delay queue on the device, adding ``clsact`` at the stop lost all 200 queued packets uncounted,
while with ``clsact`` already in place all 200 packets sent before the stop arrived.
"""

from __future__ import annotations

from dataclasses import dataclass

from pyroute2 import IPRoute

from node_agent import kernel_verifier, loss
from node_agent.kernel_constants import SHAPER_CLASS_HANDLE
from node_agent.namespace_ops import TRANSMIT_LEAF_HANDLE, _in_namespace
from node_agent.tc_dump import interface_qdiscs

# The egress hook of a clsact qdisc: TC_H_MAKE(TC_H_CLSACT, TC_H_MIN_EGRESS).
CLSACT_EGRESS = 0xFFFFFFF3
# The one admission filter: matchall at this priority, with an action that consumes the packet.
ADMISSION_FILTER_PRIO = 2
# While admission opens: a matchall that passes every packet, ahead of the admission filter, so
# the admission filter's count is final before it goes (``freeze_admission``).
OPENING_FILTER_PRIO = 1
_TC_ACT_OK = 0
_TC_ACT_STOLEN = 4


@dataclass(frozen=True, slots=True)
class AdmissionState:
    """A pod interface's admission as the kernel reports it.

    ``hook`` is the kind of qdisc on the interface's ingress side (``clsact`` when the admission
    hook is installed). ``filters`` describes every egress filter of the hook; ``stopped`` is
    True when exactly the admission filter is present. ``taken`` is the count of packets that
    filter took, None while admission is open."""

    hook: str | None
    filters: tuple[str, ...]
    stopped: bool
    taken: int | None


def _index(ipr: IPRoute, ifname: str) -> int:
    found = ipr.link_lookup(ifname=ifname)
    if not found:
        raise FileNotFoundError(f"interface {ifname} not found")
    return int(found[0])


def _egress_filters(ipr: IPRoute, index: int) -> list:
    """The filters on the hook's egress side. A dump also lists each priority's classifier
    head, which carries no options and holds no rule; only filters with options are rules."""
    return [
        message
        for message in ipr.get_filters(index=index, parent=CLSACT_EGRESS)
        if message.get_attr("TCA_OPTIONS") is not None
    ]


def _gact(message, action: int) -> int | None:
    """The packet count of a matchall filter whose single action is gact ``action``, None when
    it has another shape."""
    if message.get_attr("TCA_KIND") != "matchall":
        return None
    options = message.get_attr("TCA_OPTIONS")
    acts = options.get_attr("TCA_MATCHALL_ACT") if options is not None else None
    if acts is None:
        return None
    actions = [value for name, value in acts.get("attrs", []) if name.startswith("TCA_ACT_PRIO")]
    if len(actions) != 1 or actions[0].get_attr("TCA_ACT_KIND") != "gact":
        return None
    parms = actions[0].get_attr("TCA_ACT_OPTIONS").get_attr("TCA_GACT_PARMS")
    if parms is None or parms["action"] != action:
        return None
    stats = actions[0].get_attr("TCA_ACT_STATS")
    basic = stats.get_attr("TCA_STATS_BASIC") if stats is not None else None
    return int(basic["packets"]) if basic is not None else 0


def _prio(message) -> int:
    return int(message["info"]) >> 16


def _taken_count(message) -> int | None:
    """The packet count of the admission filter (the consuming matchall at its priority),
    None for any other filter."""
    if _prio(message) != ADMISSION_FILTER_PRIO:
        return None
    return _gact(message, _TC_ACT_STOLEN)


def _read(ipr: IPRoute, ifname: str) -> AdmissionState:
    index = _index(ipr, ifname)
    hook = kernel_verifier.ingress_qdisc_kind(index)
    if hook != "clsact":
        return AdmissionState(hook=hook, filters=(), stopped=False, taken=None)
    filters = _egress_filters(ipr, index)
    counts = [_taken_count(message) for message in filters]
    stopped = len(filters) == 1 and counts[0] is not None
    return AdmissionState(
        hook=hook,
        filters=tuple(repr(message) for message in filters),
        stopped=stopped,
        taken=counts[0] if stopped else None,
    )


def read_admission(pid: int, ifname: str) -> AdmissionState:
    """The admission state of a pod interface."""
    return _in_namespace(pid, lambda ipr: _read(ipr, ifname))


def _install_hook(ipr: IPRoute, ifname: str) -> None:
    index = _index(ipr, ifname)
    kind = kernel_verifier.ingress_qdisc_kind(index)
    if kind == "clsact":
        return
    if kind is not None:
        raise kernel_verifier.KernelStateConflict(
            f"admission hook on {ifname}",
            (f"{ifname} ingress ({kind})",),
            (f"the ingress side of {ifname} holds a {kind} qdisc",),
        )
    ipr.tc("add", kind="clsact", index=index)


def install_stopped_hook_with(ipr: IPRoute, ifname: str) -> None:
    """Give an interface in the namespace ``ipr`` speaks to its admission hook with admission
    stopped, or reuse the hook it carries and stop it. Called where the interface is created,
    before it can forward: a long-delay end admits nothing until its contact opens (OPEN), so no
    packet crosses the link before both receive paths delay it. Adding the ``clsact`` qdisc to
    an interface that holds queued packets empties its transmit queue without counting them
    (kernel 6.8: 58 queued, none counted as dropped), so the hook goes on before the interface
    carries traffic, and a stop never adds it. A ``clsact`` qdisc is reused as
    it stands; any other qdisc on the interface's ingress side is refused, and nothing is
    replaced."""
    _install_hook(ipr, ifname)
    _stop(ipr, ifname)


def install_stopped_hook(pid: int, ifname: str) -> None:
    """``install_stopped_hook_with`` on a pod interface, by the pod's pid."""
    _in_namespace(pid, lambda ipr: install_stopped_hook_with(ipr, ifname))


def stop_admission(pid: int, ifname: str) -> None:
    """Stop a pod interface admitting packets: the consuming filter on its admission hook.

    An interface whose admission is already stopped is left as it is; a hook holding any other
    filter is refused.
    """
    _in_namespace(pid, lambda ipr: _stop(ipr, ifname))


@dataclass(frozen=True, slots=True)
class Purge:
    """A stop's purge of a transmitter: the packets it dropped, and the transmit root qdisc's
    drop count before it (the rate queue's drops at its limit until the stop)."""

    purged: int
    dropped_before: int


def purge_untransmitted(pid: int, ifname: str) -> Purge:
    """Drop a stopped long-delay transmitter's untransmitted backlog and return its count.

    The backlog is the packets waiting behind the interface's rate shaper: admitted before the
    stop, never transmitted before the contact ended. Deleting the rate class's explicit leaf
    (``namespace_ops.TRANSMIT_LEAF_HANDLE``) drops them in one kernel operation under the qdisc
    lock, which adds their number to the root qdisc's drops; with admission stopped nothing else
    enters or drops there, so the drop counter's rise is the count, exact. A packet dequeued
    before that operation has left the terminal and is in flight. Rejected alternatives: reading
    the queue length and then purging (packets leave in between, so the read is a sample), and
    bringing the pod interface down (a veth's carrier loss reaches its peer, the host-side veth
    holding this end's incoming delay queue).

    An interface whose leaf is already gone (a repeated stop) purges nothing. The transmit queue
    must be empty afterwards."""

    def _op(ipr: IPRoute) -> Purge:
        state = _read(ipr, ifname)
        if not state.stopped:
            raise kernel_verifier.KernelStateConflict(
                f"purge of {ifname}",
                state.filters,
                (f"admission on {ifname} is not stopped; packets would enter during the purge",),
            )
        index = _index(ipr, ifname)
        leaf = next(
            (
                q
                for q in interface_qdiscs(index)
                if q["parent"] == SHAPER_CLASS_HANDLE and q["handle"] == TRANSMIT_LEAF_HANDLE
            ),
            None,
        )
        before = loss.root_counts(ipr, ifname)
        purged = 0
        if leaf is not None:
            ipr.tc("del", index=index, handle=TRANSMIT_LEAF_HANDLE, parent=SHAPER_CLASS_HANDLE)
            after = loss.root_counts(ipr, ifname)
            purged = after.dropped - before.dropped
        remaining = loss.root_counts(ipr, ifname)
        if remaining.held:
            raise RuntimeError(
                f"{ifname} still holds {remaining.held} packets after its untransmitted "
                "backlog was purged"
            )
        return Purge(purged=purged, dropped_before=before.dropped)

    return _in_namespace(pid, _op)


def _stop(ipr: IPRoute, ifname: str) -> None:
    state = _read(ipr, ifname)
    if state.hook != "clsact":
        raise kernel_verifier.KernelStateConflict(
            f"admission stop on {ifname}",
            (f"{ifname} ingress ({state.hook})",),
            (f"{ifname} has no admission hook",),
        )
    if state.stopped:
        return
    if state.filters:
        raise kernel_verifier.KernelStateConflict(
            f"admission stop on {ifname}",
            state.filters,
            (f"the admission hook on {ifname} holds other filters",),
        )
    ipr.tc(
        "add-filter",
        kind="matchall",
        index=_index(ipr, ifname),
        parent=CLSACT_EGRESS,
        prio=ADMISSION_FILTER_PRIO,
        action={"kind": "gact", "action": "stolen"},
    )


def _opening_filters(ipr: IPRoute, ifname: str) -> tuple[object | None, object | None]:
    """The admission filter and the opening filter on the hook, each None when absent; any other
    filter is refused."""
    if kernel_verifier.ingress_qdisc_kind(_index(ipr, ifname)) != "clsact":
        raise kernel_verifier.KernelStateConflict(
            f"admission open on {ifname}", (), (f"{ifname} has no admission hook",)
        )
    admitting = None
    passing = None
    for message in _egress_filters(ipr, _index(ipr, ifname)):
        if _taken_count(message) is not None:
            admitting = message
        elif _prio(message) == OPENING_FILTER_PRIO and _gact(message, _TC_ACT_OK) is not None:
            passing = message
        else:
            raise kernel_verifier.KernelStateConflict(
                f"admission open on {ifname}",
                (repr(message),),
                (f"the admission hook on {ifname} holds other filters",),
            )
    return admitting, passing


def freeze_admission(pid: int, ifname: str) -> int | None:
    """The final count of a stopped interface's admission filter: a pass filter goes in front
    of it first, in one kernel operation, so from then on every packet passes and the admission
    filter counts nothing more. Rejected alternative: reading the count and then removing the
    filter, which loses the packets it takes in between. Repeatable; None when admission is
    open with neither filter present."""

    def _op(ipr: IPRoute) -> int | None:
        admitting, passing = _opening_filters(ipr, ifname)
        if admitting is None:
            return None
        if passing is None:
            ipr.tc(
                "add-filter",
                kind="matchall",
                index=_index(ipr, ifname),
                parent=CLSACT_EGRESS,
                prio=OPENING_FILTER_PRIO,
                action={"kind": "gact", "action": "ok"},
            )
        admitting, _passing = _opening_filters(ipr, ifname)
        return _taken_count(admitting)

    return _in_namespace(pid, _op)


def open_admission(pid: int, ifname: str) -> None:
    """Let a pod interface admit packets again after ``freeze_admission``: remove the
    admission filter, then the pass filter. Repeatable."""

    def _op(ipr: IPRoute) -> None:
        admitting, passing = _opening_filters(ipr, ifname)
        if admitting is not None and passing is None:
            raise kernel_verifier.KernelStateConflict(
                f"admission open on {ifname}",
                (repr(admitting),),
                (f"admission on {ifname} opens only after its count is frozen",),
            )
        for message, prio in ((admitting, ADMISSION_FILTER_PRIO), (passing, OPENING_FILTER_PRIO)):
            if message is not None:
                ipr.tc(
                    "del-filter",
                    kind="matchall",
                    index=_index(ipr, ifname),
                    parent=CLSACT_EGRESS,
                    prio=prio,
                )

    _in_namespace(pid, _op)


def verify_admission_hook(pid: int, ifname: str, *, stopped: bool) -> kernel_verifier.Proof:
    """Prove the admission hook on a pod interface and whether admission is stopped."""
    try:
        state = read_admission(pid, ifname)
    except Exception as exc:
        return kernel_verifier.Proof.fail(f"admission proof failed for {ifname}", str(exc))
    evidence = (f"pid={pid}", f"ifname={ifname}", f"hook={state.hook}", *state.filters)
    if state.hook != "clsact":
        return kernel_verifier.Proof.fail(f"missing admission hook on {ifname}", *evidence)
    if stopped and not state.stopped:
        return kernel_verifier.Proof.fail(f"admission not stopped on {ifname}", *evidence)
    if not stopped and state.filters:
        return kernel_verifier.Proof.fail(f"admission not open on {ifname}", *evidence)
    summary = "admission stopped" if stopped else "admission open"
    return kernel_verifier.Proof.ok(f"{summary} on {ifname}", *evidence)
