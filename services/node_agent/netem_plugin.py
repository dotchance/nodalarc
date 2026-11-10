# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""pyroute2's netem plugin with netem's 64-bit delay attribute.

The netem options struct carries the delay as a 32-bit count of scheduler
ticks. On the development servers a tick is 64 ns (``/proc/net/psched`` gives
15.625 ticks per microsecond), so that field holds at most 274.9 s. Light time
between Earth and Mars runs from about 182 s to 1,342 s. The kernel also takes
and reports the delay in nanoseconds in the 64-bit attribute
``TCA_NETEM_LATENCY64``, and it uses that attribute in place of the 32-bit
field when a request carries both (``netem_change`` in
``net/sched/sch_netem.c``).

pyroute2 0.9.6 maps netem's attributes only up to ``TCA_NETEM_RATE``. Through
its own plugin, a delay of 275 s failed to encode with a range error, and a
dump's 64-bit attribute was not decoded. This module is a netem plugin whose
attribute map adds the kernel's attributes up to ``TCA_NETEM_JITTER64``
(``include/uapi/linux/pkt_sched.h``) and whose requests carry the delay in
nanoseconds. ``register()`` puts it in pyroute2's plugin table in place of
pyroute2's own netem plugin; pyroute2 looks the plugin up there for every netem
request it encodes and every netem dump it decodes. Every link uses this one
encoding, so links of every delay are written and proven the same way.

Rejected alternatives. Extending or subclassing pyroute2's own netem options
class takes effect only if no netem message was encoded or decoded earlier in
the process: pyroute2 compiles a class's attribute map once, at its first
instance, and a subclass inherits the compiled map of a parent that was used
first (measured: a subclass made after one use of the parent encoded no 64-bit
attribute). The options class here derives from ``nla`` directly, so it
compiles its own map. Building the netem request by hand would duplicate
pyroute2's netem encoder, and running iproute2's ``tc`` starts a child process,
which the Node Agent never does.
"""

from __future__ import annotations

import sys
from typing import Any

from pyroute2.netlink import nla
from pyroute2.netlink.rtnl.tcmsg import common as tc_common
from pyroute2.netlink.rtnl.tcmsg import plugins, sched_netem

# The largest value of the options struct's 32-bit delay field, in ticks.
_DELAY_FIELD_MAX = 0xFFFFFFFF

parent = sched_netem.parent


class options(nla):
    """Netem's TCA_OPTIONS: the options struct, then the attributes in the
    kernel's numbering. pyroute2 finds this class by its name, ``options``."""

    nla_map = (
        *sched_netem.options.nla_map,
        ("TCA_NETEM_ECN", "uint32"),
        ("TCA_NETEM_RATE64", "uint64"),
        ("TCA_NETEM_PAD", "none"),
        ("TCA_NETEM_LATENCY64", "int64"),
        ("TCA_NETEM_JITTER64", "int64"),
    )
    fields = sched_netem.options.fields
    netem_corr = sched_netem.options.netem_corr
    netem_reorder = sched_netem.options.netem_reorder
    netem_corrupt = sched_netem.options.netem_corrupt
    netem_rate = sched_netem.options.netem_rate


def get_parameters(kwarg: dict[str, Any]) -> dict[str, Any]:
    """The netem options of a request whose delay is ``delay_ns`` nanoseconds.

    A request that names ``delay`` is refused: pyroute2's own unit for it is
    microseconds in the 32-bit field, and the delay has one unit here. The
    32-bit field carries the delay in whole ticks, saturated at its largest
    value; the kernel takes the 64-bit attribute in its place.
    """
    if "delay" in kwarg or "delay_ns" not in kwarg:
        raise ValueError("a netem request carries its delay in nanoseconds, as delay_ns")
    delay_ns = kwarg["delay_ns"]
    if not isinstance(delay_ns, int) or delay_ns < 0:
        raise ValueError(f"netem delay must be a non-negative integer of ns, got {delay_ns!r}")
    opts = sched_netem.get_parameters({k: v for k, v in kwarg.items() if k != "delay_ns"})
    opts["delay"] = min(int(delay_ns * tc_common.tick_in_usec / 1000), _DELAY_FIELD_MAX)
    opts["attrs"].append(["TCA_NETEM_LATENCY64", delay_ns])
    return opts


def latency_ns(netem_options: nla | None) -> int | None:
    """The delay a netem qdisc reports in TCA_NETEM_LATENCY64, in nanoseconds.

    None when the dump carries no such attribute; the 32-bit field is not read
    in its place, because above 274.9 s it reports its saturated value.
    """
    if netem_options is None:
        return None
    value = netem_options.get_attr("TCA_NETEM_LATENCY64")
    return value if isinstance(value, int) else None


def register() -> None:
    """Make this module pyroute2's netem plugin for the whole process."""
    plugins["netem"] = sys.modules[__name__]
