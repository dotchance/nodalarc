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
carries traffic, and stopping or resuming admission changes only the filter; local links carry no
hook and keep their LinkDown unchanged. A stopped admission also freezes what enters a link end's
queues, which is what lets the loss at a queue's reset be counted exactly. Rejected alternative: adding ``clsact`` when
admission stops. Grafting a qdisc onto a device resets the device's queues; measured with a
delay queue on the device, adding ``clsact`` at the stop lost all 200 queued packets uncounted,
while with ``clsact`` already in place all 200 packets sent before the stop arrived.
"""

from __future__ import annotations

from dataclasses import dataclass

from pyroute2 import IPRoute

from node_agent import kernel_verifier
from node_agent.namespace_ops import _in_namespace

# The egress hook of a clsact qdisc: TC_H_MAKE(TC_H_CLSACT, TC_H_MIN_EGRESS).
CLSACT_EGRESS = 0xFFFFFFF3
# The one admission filter: matchall at this priority, with an action that consumes the packet.
ADMISSION_FILTER_PRIO = 1
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


def _taken_count(message) -> int | None:
    """The packet count of a matchall filter's single consuming action, None when it has
    another shape."""
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
    if parms is None or parms["action"] != _TC_ACT_STOLEN:
        return None
    stats = actions[0].get_attr("TCA_ACT_STATS")
    basic = stats.get_attr("TCA_STATS_BASIC") if stats is not None else None
    return int(basic["packets"]) if basic is not None else 0


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


def install_admission_hook(pid: int, ifname: str) -> None:
    """Give a pod interface its admission hook, or reuse the hook it carries.

    Called where the interface is created, before it carries traffic. A ``clsact`` qdisc is
    reused as it stands; any other qdisc on the interface's ingress side is refused, and nothing
    is replaced.
    """

    def _op(ipr: IPRoute) -> None:
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

    _in_namespace(pid, _op)


def stop_admission(pid: int, ifname: str) -> None:
    """Stop a pod interface admitting packets: the consuming filter on its admission hook.

    An interface whose admission is already stopped is left as it is; a hook holding any other
    filter is refused.
    """

    def _op(ipr: IPRoute) -> None:
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

    _in_namespace(pid, _op)


def open_admission(pid: int, ifname: str) -> int | None:
    """Let a pod interface admit packets again: remove the filter from its admission hook.

    Returns the packets the filter took while admission was stopped, read before the filter
    goes; None when admission was already open. A hook holding any other filter is refused.
    """

    def _op(ipr: IPRoute) -> int | None:
        state = _read(ipr, ifname)
        if state.hook != "clsact":
            raise kernel_verifier.KernelStateConflict(
                f"admission open on {ifname}",
                (f"{ifname} ingress ({state.hook})",),
                (f"{ifname} has no admission hook",),
            )
        if not state.filters:
            return None
        if not state.stopped:
            raise kernel_verifier.KernelStateConflict(
                f"admission open on {ifname}",
                state.filters,
                (f"the admission hook on {ifname} holds other filters",),
            )
        ipr.tc(
            "del-filter",
            kind="matchall",
            index=_index(ipr, ifname),
            parent=CLSACT_EGRESS,
            prio=ADMISSION_FILTER_PRIO,
        )
        return state.taken

    return _in_namespace(pid, _op)


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
