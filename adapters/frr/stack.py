# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""FRR's routing stack for one resolved routing domain.

The stack is the FRR adapter's selection for a domain: which FRR daemons run,
which configuration fragments assemble into ``frr.conf`` and in what order,
and the stack-level template inputs. It is derived from the domain's
protocol, capabilities and BFD setting alone. A member runs the stack's
daemons and fragments for the address families the session gives it: the
IPv6-only entries (OSPFv3 beside OSPFv2) run on IPv6 members only.

Kernel requirements (MPLS labels, TTL propagation) are engine-neutral
substrate facts and live in ``nodalarc.substrate.routing_requirements``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from adapters.frr.support import FRR_SUPPORT

if TYPE_CHECKING:
    from nodalarc.models.resolved_session import ResolvedRoutingDomain

# Segment Routing Global and Local Blocks the FRR stacks advertise.
_SRGB_START = 16000
_SRGB_END = 23999
_SRLB_START = 40000
_SRLB_END = 49999


@dataclass(frozen=True)
class ResolvedStack:
    """FRR's selection for one routing domain.

    ``daemons`` are the FRR daemons the domain's members run, in the order
    they were selected. ``fragments`` name the configuration templates that
    assemble into ``frr.conf``, in assembly order. ``ipv6_only`` names the
    daemons and fragments only IPv6 members run. ``image``, ``mi_adapter``
    and ``max_compression`` are read by the measurement service only.
    """

    daemons: tuple[str, ...]
    fragments: tuple[str, ...]
    template_variables: dict[str, Any]
    image: str
    mi_adapter: str | None
    segment_routing: bool
    ipv6_only: frozenset[str] = frozenset()
    max_compression: int = 10

    def member_daemons(self, address_families: frozenset[str]) -> tuple[str, ...]:
        """The daemons one member runs for the address families it carries."""
        return self._for_families(self.daemons, address_families)

    def member_fragments(self, address_families: frozenset[str]) -> tuple[str, ...]:
        """The fragments one member's ``frr.conf`` assembles, in order."""
        return self._for_families(self.fragments, address_families)

    def _for_families(
        self, selected: tuple[str, ...], address_families: frozenset[str]
    ) -> tuple[str, ...]:
        if "ipv6" in address_families:
            return selected
        return tuple(name for name in selected if name not in self.ipv6_only)


def validate_sid_indices(stack: ResolvedStack, sid_by_node: Mapping[str, int]) -> None:
    """Validate resolver-owned prefix-SID indices against the stack SRGB.

    The resolver owns SID allocation. The stack owns only the SRGB size and
    whether segment routing is enabled. No caller derives SID indices from
    plane/slot, node kind, or ground-station order.
    """
    if not stack.segment_routing:
        return
    if not sid_by_node:
        raise ValueError("segment routing requires resolved SID indices")
    srgb_size = stack.template_variables["srgb_end"] - stack.template_variables["srgb_start"] + 1
    invalid = {node_id: sid for node_id, sid in sid_by_node.items() if sid <= 0 or sid > srgb_size}
    if invalid:
        examples = ", ".join(f"{node_id}={sid}" for node_id, sid in sorted(invalid.items())[:10])
        raise ValueError(f"resolved SID index exceeds SRGB size {srgb_size}: {examples}")


def resolve_domain_stack(domain: ResolvedRoutingDomain) -> ResolvedStack:
    """Select FRR's daemons, fragments and stack inputs for one domain.

    A domain outside the FRR support declaration is a loud failure: the
    resolver refuses such a session before any rendering, so reaching here
    with one is a contract violation.
    """
    protocol_support = FRR_SUPPORT.routing.get(domain.protocol)
    if protocol_support is None:
        raise ValueError(
            f"routing domain {domain.domain_id!r} uses protocol {domain.protocol!r}, "
            "which the FRR adapter does not render"
        )
    capabilities = frozenset(domain.capabilities)
    unrendered = sorted(capabilities - protocol_support.capabilities)
    if unrendered:
        raise ValueError(
            f"routing domain {domain.domain_id!r} declares capabilities {unrendered} "
            f"that the FRR adapter does not render for {domain.protocol!r}"
        )
    bfd_enabled = domain.timers.bfd.enabled
    if bfd_enabled and protocol_support.bfd is None:
        raise ValueError(
            f"routing domain {domain.domain_id!r} enables BFD, which the FRR adapter does "
            f"not render for {domain.protocol!r}"
        )

    # mgmtd loads the integrated configuration in FRR 10; zebra owns
    # interfaces and the RIB. Both run on every FRR node.
    daemons = ["mgmtd", "zebra"]
    fragments = ["global", "zebra"]
    template_vars: dict[str, Any] = {
        "protocol": domain.protocol,
        "log_file": None,
        "sr_enabled": False,
        "te_enabled": False,
    }
    if bfd_enabled:
        # The BFD profile precedes the IGP interfaces that reference it.
        daemons.append("bfdd")
        fragments.append("bfdd")
    ipv6_only: set[str] = set()
    if domain.protocol in {"isis", "ospf"}:
        igp_daemon = f"{domain.protocol}d"
        daemons.append(igp_daemon)
        fragments.append(igp_daemon)
        # Every daemon logs to the IGP's log file in the integrated
        # configuration; the measurement adapters tail it.
        template_vars["log_file"] = f"/var/log/frr/{igp_daemon}.log"
    if domain.protocol == "ospf":
        # OSPFv2 routes IPv4 only; IPv6 members also run OSPFv3.
        daemons.append("ospf6d")
        fragments.append("ospf6d")
        ipv6_only.add("ospf6d")
    segment_routing = "segment_routing" in capabilities
    if segment_routing:
        # SR-MPLS provides the MPLS data plane; LDP does not run.
        daemons.append("pathd")
        fragments.append("pathd")
        template_vars.update(
            {
                "sr_enabled": True,
                "srgb_start": _SRGB_START,
                "srgb_end": _SRGB_END,
                "srlb_start": _SRLB_START,
                "srlb_end": _SRLB_END,
            }
        )
    elif "mpls" in capabilities:
        # A bare mpls capability means LDP-distributed labels.
        daemons.append("ldpd")
        fragments.append("ldpd")
    if "traffic_engineering" in capabilities:
        template_vars["te_enabled"] = True
    daemons.append("staticd")
    fragments.append("staticd")

    return ResolvedStack(
        daemons=tuple(daemons),
        fragments=tuple(fragments),
        template_variables=template_vars,
        image="frr",
        mi_adapter={"isis": "frr_isis_adapter", "ospf": "frr_ospf_adapter"}.get(domain.protocol),
        segment_routing=segment_routing,
        ipv6_only=frozenset(ipv6_only),
    )
