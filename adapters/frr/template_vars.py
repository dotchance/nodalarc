# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""FRR template inputs for one routed node of a resolved session."""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING, Any

from nodalarc.models.resolved_session import ROUTING_AREA_PROTOCOLS

if TYPE_CHECKING:
    from nodalarc.models.resolved_session import (
        ResolvedNode,
        ResolvedRoutingDomain,
        ResolvedSession,
    )

    from adapters.frr.stack import ResolvedStack

# Reference bandwidth for fixed-link IGP metrics: the metric of a fixed link
# is this value over the transmit rate of the interface's own terminal,
# truncated to an integer. Each end of a link sends at its own rate, so the
# two ends of an asymmetric link carry different metrics.
_REFERENCE_BANDWIDTH_MBPS = 10000
# IGP metric of an access (ground) link.
_ACCESS_LINK_METRIC = 10
# Share of a terminal's transmit rate advertised as reservable, and as
# unreserved at every priority, in MPLS-TE link parameters.
_TE_RESERVABLE_FRACTION = 0.98


def build_template_vars_from_resolved(
    resolved: ResolvedSession,
    node: ResolvedNode,
    *,
    domain: ResolvedRoutingDomain,
    stack: ResolvedStack,
    node_sid_index: int | None,
) -> dict[str, Any]:
    """Build one node's FRR template inputs from the resolved runtime view."""
    if node.interfaces is None:
        raise ValueError(f"resolved node {node.node_id!r} has no interface addresses")
    result: dict[str, Any] = dict(stack.template_variables)
    if result.get("sr_enabled"):
        if node_sid_index is None:
            raise ValueError(
                f"segment routing is enabled but no resolved SID index was provided for "
                f"{node.node_id}"
            )
        result["node_sid_index"] = node_sid_index

    result.update(_timer_template_facts(domain))
    segment_interfaces, default_route, default_metric = _segment_template_facts(
        resolved, node, domain
    )
    result["segment_interfaces"] = segment_interfaces
    result["default_route_originate"] = default_route
    result["default_route_metric"] = default_metric
    if domain.protocol in ROUTING_AREA_PROTOCOLS:
        result["area_id"] = domain.area_id_for(node)
    result.update(
        {
            "hostname": node.node_id,
            "system_id": _isis_system_id(resolved, node),
            "ipv4_loopback": _ip_from_interface(node.interfaces.lo0.ipv4, field="lo0.ipv4"),
            # v4-only nodes are a legitimate resolved shape; templates render
            # the v6 loopback only when the resolver assigned one.
            "ipv6_loopback": (
                _ip_from_interface(node.interfaces.lo0.ipv6, field="lo0.ipv6")
                if node.interfaces.lo0.ipv6 is not None
                else None
            ),
            "wan_interfaces": _wan_interfaces(
                resolved, node, domain, te_enabled=stack.template_variables["te_enabled"]
            ),
        }
    )
    boundary_routes = _boundary_static_routes(resolved, node, domain)
    result["boundary_static_routes"] = boundary_routes
    # Border nodes must redistribute boundary statics into their IGP, or the
    # rest of the domain never learns the exported reachability.
    result["redistribute_static"] = bool(boundary_routes) and domain.protocol in {"isis", "ospf"}
    return result


def _access_interface_names(node: ResolvedNode) -> list[str]:
    """The node's access (ground-link) WAN interfaces."""
    if node.kind == "satellite":
        return [iface.name for iface in node.wan_interfaces if iface.name.startswith("gnd")]
    if node.kind == "ground_station":
        return [iface.name for iface in node.wan_interfaces]
    raise ValueError(f"unsupported resolved node kind for FRR rendering: {node.kind!r}")


def _transmit_mbps(node: ResolvedNode, interface: str, *, purpose: str) -> float:
    """The transmit rate of the terminal behind one WAN interface.

    This is what this end can send over the link, whatever terminal the peer
    carries.
    """
    terminal_id = next(
        (wan.terminal_id for wan in node.wan_interfaces if wan.name == interface), None
    )
    block = next(
        (item for item in node.terminal_inventory if item.terminal_id == terminal_id), None
    )
    if block is None or block.transmit_mbps is None:
        raise ValueError(
            f"node {node.node_id!r} interface {interface!r} has no terminal transmit rate "
            f"for its {purpose}"
        )
    return block.transmit_mbps


def _te_link_params(node: ResolvedNode, interface: str) -> dict[str, str]:
    """MPLS-TE bandwidth for one WAN interface, from its own terminal.

    Maximum bandwidth is the terminal's transmit rate. FRR takes bytes per
    second.
    """
    transmit_mbps = _transmit_mbps(node, interface, purpose="traffic-engineering link parameters")
    max_bw = transmit_mbps * 1_000_000 / 8
    return {
        "max_bw": f"{max_bw:g}",
        "reservable_bw": f"{max_bw * _TE_RESERVABLE_FRACTION:g}",
    }


def _wan_interfaces(
    resolved: ResolvedSession,
    node: ResolvedNode,
    domain: ResolvedRoutingDomain,
    *,
    te_enabled: bool,
) -> list[dict[str, Any]]:
    """Every WAN interface the node's FRR configuration names, once each.

    Fixed links come from the node's non-access link candidates, in candidate
    order; access links follow. ``static_only`` marks a fixed link that a
    ``static_ip`` routing boundary crosses: it carries static routes and no
    IGP. ``bfd`` marks every IGP link of a BFD-enabled domain; ``te`` carries
    the MPLS-TE link parameters of every IGP link of a TE domain. ``ospf_area``
    is present for OSPF domains: a fixed link whose peer sits in another
    domain or area, and every access link, run in the backbone.
    """
    static_rules = _static_boundary_rule_ids(resolved)
    is_ospf = domain.protocol == "ospf"
    node_area = domain.area_id_for(node) if is_ospf else None
    bfd_enabled = domain.timers.bfd.enabled
    interfaces: list[dict[str, Any]] = []
    for candidate in resolved.link_candidates:
        if candidate.kind == "access":
            continue
        interface_a, interface_b = candidate.fixed_interfaces
        if candidate.node_a == node.node_id:
            name, peer_id = interface_a, candidate.node_b
        elif candidate.node_b == node.node_id:
            name, peer_id = interface_b, candidate.node_a
        else:
            continue
        peer = resolved.node_by_id(peer_id)
        if peer is None or peer.interfaces is None:
            raise ValueError(
                f"link candidate {candidate.rule_id!r} references unresolved peer {peer_id!r}"
            )
        static_only = candidate.rule_id in static_rules
        entry: dict[str, Any] = {
            "name": name,
            "static_only": static_only,
            # A static_ip boundary link carries no IGP, so no IGP BFD or TE.
            "bfd": bfd_enabled and not static_only,
            "te": _te_link_params(node, name) if te_enabled and not static_only else None,
            "metric": int(
                _REFERENCE_BANDWIDTH_MBPS / _transmit_mbps(node, name, purpose="IGP metric")
            ),
            "peer_loopback_ipv4": _ip_from_interface(
                peer.interfaces.lo0.ipv4,
                field=f"{peer.node_id}.lo0.ipv4",
            ),
        }
        # The peer is a router in exactly one domain whatever the protocol;
        # OSPF also reads its area.
        peer_domain = resolved.routing_domain_for(peer_id)
        if is_ospf:
            cross_area = domain.domain_id != peer_domain.domain_id or node_area != (
                peer_domain.area_id_for(peer)
            )
            entry["ospf_area"] = "0.0.0.0" if cross_area else node_area
        interfaces.append(entry)
    for name in _access_interface_names(node):
        entry = {
            "name": name,
            "static_only": False,
            "bfd": bfd_enabled,
            "te": _te_link_params(node, name) if te_enabled else None,
            "metric": _ACCESS_LINK_METRIC,
        }
        if is_ospf:
            entry["ospf_area"] = "0.0.0.0"
        interfaces.append(entry)
    names = [entry["name"] for entry in interfaces]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(
            f"node {node.node_id!r} names WAN interface(s) {duplicates} as both a fixed "
            "link and an access link"
        )
    return interfaces


def _segment_peer_count(
    resolved: ResolvedSession,
    node: ResolvedNode,
    domain: ResolvedRoutingDomain,
    interface: str,
) -> int:
    """Routed peers sharing this interface's segment within the same domain."""
    for segment in resolved.ethernet_segments:
        member_ids = {member.node_id for member in segment.members}
        if not any(
            member.node_id == node.node_id and member.interface == interface
            for member in segment.members
        ):
            continue
        return sum(
            1
            for peer in resolved.nodes
            if peer.node_id != node.node_id
            and peer.node_id in member_ids
            and peer.forwarding == "routed"
            and peer.node_id in domain.node_ids
        )
    return 0


def _timer_template_facts(domain: ResolvedRoutingDomain) -> dict[str, Any]:
    """Map resolved per-domain timers onto the protocol's FRR vocabulary.

    The resolver always populates ``timers`` with effective values, so every
    emitted fact is concrete — templates carry no timer fallbacks.
    """
    timers = domain.timers
    facts: dict[str, Any] = {
        "bfd_detect_multiplier": timers.bfd.detect_multiplier,
        "bfd_rx_interval": timers.bfd.rx_interval_ms,
        "bfd_tx_interval": timers.bfd.tx_interval_ms,
    }
    if domain.protocol == "isis":
        facts.update(
            {
                "isis_hello_interval": timers.hello_interval_s,
                "isis_hello_multiplier": max(
                    2, -(-timers.hold_interval_s // timers.hello_interval_s)
                ),
                "spf_init_delay": timers.spf.init_delay_ms,
                "spf_short_delay": timers.spf.short_delay_ms,
                "spf_long_delay": timers.spf.long_delay_ms,
                "spf_holddown": timers.spf.holddown_ms,
                "spf_time_to_learn": timers.spf.time_to_learn_ms,
            }
        )
    elif domain.protocol == "ospf":
        facts.update(
            {
                "ospf_hello_interval": timers.hello_interval_s,
                "ospf_dead_interval": timers.hold_interval_s,
                "ospf_spf_delay": timers.spf.init_delay_ms,
                "ospf_spf_initial_hold": timers.spf.short_delay_ms,
                "ospf_spf_max_hold": timers.spf.long_delay_ms,
            }
        )
    return facts


def _isis_system_id(resolved: ResolvedSession, node: ResolvedNode) -> str:
    """Globally-unique IS-IS system ID from the resolver-owned node index.

    Plane/slot restart at zero per segment, so they are never identity. The
    resolution-order node index is unique across the whole session; templates
    consume the formatted value verbatim and derive nothing.

    The id space starts at one, not zero: the all-zero system id is reserved
    in IS-IS, and FRR floods an LSP from such a router while remote SPF never
    installs its prefixes, leaving the node reachable only by direct
    neighbors.
    """
    value = resolved.node_index_by_node_id()[node.node_id] + 1
    return f"0000.{(value >> 16) & 0xFFFF:04x}.{value & 0xFFFF:04x}"


def _static_boundary_rule_ids(resolved: ResolvedSession) -> set[str]:
    if resolved.routing is None or not resolved.routing.boundaries:
        return set()
    return {
        boundary.over for boundary in resolved.routing.boundaries if boundary.adapter == "static_ip"
    }


def _lo0_address(node: ResolvedNode, family: str) -> str | None:
    if node.interfaces is None:
        return None
    value = getattr(node.interfaces.lo0, family)
    return value.split("/")[0] if value is not None else None


def _boundary_export_prefixes(
    export: Any, from_domain: ResolvedRoutingDomain, resolved: ResolvedSession
) -> dict[str, list[str]]:
    """The concrete per-family prefix set one export rule declares.

    ``aggregate_of: originated`` derives the from-domain's originated
    prefixes (grammar C046). Literal prefix lists pass through split by
    family.
    """
    prefixes: dict[str, list[str]] = {"ipv4": [], "ipv6": []}
    declared = export.prefixes
    if isinstance(declared, tuple):
        for prefix in declared:
            prefixes["ipv6" if ":" in prefix else "ipv4"].append(prefix)
    else:  # AggregateOf — validated at resolve
        seen: set[str] = set()
        for node_id in from_domain.node_ids:
            node = resolved.node_by_id(node_id)
            if node is None or node.originated_prefixes is None:
                continue
            for family in ("ipv4", "ipv6"):
                for prefix in getattr(node.originated_prefixes, family) or ():
                    if prefix not in seen:
                        seen.add(prefix)
                        prefixes[family].append(prefix)
    if export.export_node_loopbacks:
        for node_id in from_domain.node_ids:
            node = resolved.node_by_id(node_id)
            if node is None or node.forwarding != "routed":
                continue
            for family, host_len in (("ipv4", 32), ("ipv6", 128)):
                address = _lo0_address(node, family)
                if address is not None:
                    prefix = f"{address}/{host_len}"
                    if prefix not in prefixes[family]:
                        prefixes[family].append(prefix)
    return prefixes


def _boundary_static_routes(
    resolved: ResolvedSession, node: ResolvedNode, domain: ResolvedRoutingDomain
) -> list[dict[str, str]]:
    """Materialized static routes this border node installs for boundary
    exports it receives.

    The receiving side of ``from: X, to: Y`` is the Y-domain endpoint of the
    boundary rule's candidates. Next hop is the boundary peer's loopback
    (install_via: peer_loopback, the default) — recursive over the existing
    peer-loopback interface route — or the named interface. A family is
    installable only when the peer carries a loopback of that family; the
    aggregate semantics are per installable family.
    """
    if resolved.routing is None or not resolved.routing.boundaries:
        return []
    routes: list[dict[str, str]] = []
    emitted: set[tuple[str, str]] = set()
    for boundary in resolved.routing.boundaries:
        if boundary.adapter != "static_ip":
            continue
        for export in boundary.export:
            if export.to not in (domain.domain_id,):
                continue
            from_domain = next(d for d in resolved.routing_domains if d.domain_id == export.from_)
            for candidate in resolved.link_candidates:
                if candidate.rule_id != boundary.over:
                    continue
                interface_a, interface_b = candidate.fixed_interfaces
                if candidate.node_a == node.node_id:
                    peer_id, iface = candidate.node_b, interface_a
                elif candidate.node_b == node.node_id:
                    peer_id, iface = candidate.node_a, interface_b
                else:
                    continue
                if peer_id not in from_domain.node_ids:
                    continue
                peer = resolved.node_by_id(peer_id)
                if peer is None:
                    raise ValueError(f"boundary candidate references unresolved peer {peer_id!r}")
                exports = _boundary_export_prefixes(export, from_domain, resolved)
                for family in ("ipv4", "ipv6"):
                    if export.install_via is None or export.install_via == "peer_loopback":
                        via = _lo0_address(peer, family)
                        if via is None:
                            # Family not installable over this peer; the
                            # aggregate is defined per installable family.
                            # Literal prefixes of an uninstallable family are
                            # an authoring error — fail loud.
                            if isinstance(export.prefixes, tuple) and exports[family]:
                                raise ValueError(
                                    f"boundary over {boundary.over!r} exports {family} "
                                    f"prefixes but peer {peer_id!r} has no {family} "
                                    "loopback for install_via: peer_loopback"
                                )
                            continue
                    else:
                        via = iface if export.install_via == iface else export.install_via
                    for prefix in exports[family]:
                        if _lo0_address(node, family) == prefix.split("/")[0]:
                            continue  # never route our own loopback
                        if via == prefix.split("/")[0]:
                            continue  # peer's own loopback is the seed route
                        key = (prefix, via)
                        if key not in emitted:
                            emitted.add(key)
                            routes.append({"prefix": prefix, "via": via, "family": family})
    return routes


def _segment_template_facts(
    resolved: ResolvedSession,
    node: ResolvedNode,
    domain: ResolvedRoutingDomain,
) -> tuple[list[dict[str, Any]], bool, int]:
    """Per-segment interface facts for any node carrying Ethernet segments.

    A segment joins the IGP exactly when the node originates its allocated
    prefix; it runs active only where the wired segment has a routed
    same-domain peer (broadcast adjacency on a real L2 segment) and stays
    passive otherwise — the honest stub posture. BFD runs on every active
    segment of a BFD-enabled domain and never on a passive one.
    """
    if node.interfaces is None or not node.interfaces.ethernet:
        return [], _originates_default(node), 100

    originated_networks: set[str] = set()
    default_route = False
    if node.originated_prefixes is not None:
        for family in ("ipv4", "ipv6"):
            for prefix in getattr(node.originated_prefixes, family) or ():
                network = ipaddress.ip_network(prefix, strict=False)
                if network.prefixlen == 0:
                    default_route = True
                else:
                    originated_networks.add(str(network))

    interfaces: list[dict[str, Any]] = []
    covered: set[str] = set()
    for name, segment in sorted(node.interfaces.ethernet.items()):
        addresses: list[dict[str, Any]] = []
        networks: set[str] = set()
        for value in (segment.ipv4, segment.ipv6):
            if value is None:
                continue
            iface = ipaddress.ip_interface(value)
            addresses.append({"host_address": value, "metric": 10, "prefix": str(iface.network)})
            networks.add(str(iface.network))
        igp_enabled = bool(networks & originated_networks)
        igp_active = _segment_peer_count(resolved, node, domain, name) >= 1
        covered.update(networks & originated_networks)
        bfd = domain.timers.bfd.enabled and igp_enabled and igp_active
        if bfd and segment.ipv4 is None:
            raise ValueError(
                f"node {node.node_id!r} runs {domain.protocol} with BFD actively on "
                f"{name!r}, which has no IPv4 address; the FRR adapter configures the IGP "
                "and its BFD sessions over IPv4"
            )
        interfaces.append(
            {
                "name": name,
                "addresses": addresses,
                "metric": 10,
                "igp_enabled": igp_enabled,
                "igp_active": igp_active,
                "bfd": bfd,
            }
        )
    uncovered = sorted(originated_networks - covered)
    if uncovered:
        raise ValueError(
            f"node {node.node_id!r} originates non-connected prefix(es) {uncovered}; "
            "FRR rendering supports connected segment prefixes and default routes"
        )
    return interfaces, default_route, 100


def _originates_default(node: ResolvedNode) -> bool:
    if node.originated_prefixes is None:
        return False
    return any(
        ipaddress.ip_network(prefix, strict=False).prefixlen == 0
        for family in ("ipv4", "ipv6")
        for prefix in getattr(node.originated_prefixes, family) or ()
    )


def _ip_from_interface(value: str | None, *, field: str) -> str:
    if value is None:
        raise ValueError(f"required interface address is missing: {field}")
    return str(ipaddress.ip_interface(value).ip)
