# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""FRR template inputs for one router of a resolved session.

Router-wide facts (identity, loopbacks, interface addresses, static routes,
BFD profiles, LDP interfaces) sit at the top level. Everything a routing
domain decides (its protocol instance, area, timers, capabilities,
interfaces, origination and redistribution) sits in one entry per domain
the router participates in; the resolved session assigns each interface to
its domains.
"""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING, Any

from nodalarc.model_validation import ADDRESS_FAMILIES
from nodalarc.models.resolved_session import IsisInstanceAreas, OspfInstanceAreas
from nodalarc.workloads.adapter import AdapterRenderRefusal

from adapters.frr.stack import LOG_FILE, SRLB
from adapters.frr.support import FRR_MAXIMUM_IGP_METRIC, FRR_REFERENCE_BANDWIDTH_MBPS, FRR_SRGB

if TYPE_CHECKING:
    from collections.abc import Mapping

    from nodalarc.models.resolved_session import (
        InstanceAreas,
        ResolvedNode,
        ResolvedRoutingDomain,
        ResolvedSession,
    )

_MINIMUM_IGP_METRIC = 1
# IGP metric of an access (ground) link.
_ACCESS_LINK_METRIC = 10
# IGP metric of an Ethernet segment interface.
_SEGMENT_METRIC = 10
# Metric of an originated default route.
_DEFAULT_ROUTE_METRIC = 100
# Share of a terminal's transmit rate advertised as reservable, and as
# unreserved at every priority, in MPLS-TE link parameters.
_TE_RESERVABLE_FRACTION = 0.98


def build_template_vars_from_resolved(
    resolved: ResolvedSession,
    node: ResolvedNode,
    *,
    domains: tuple[ResolvedRoutingDomain, ...],
    sid_by_domain: Mapping[str, Mapping[str, int]],
) -> dict[str, Any]:
    """Build one router's FRR template inputs from the resolved runtime view."""
    if node.interfaces is None:
        raise AdapterRenderRefusal(f"resolved node {node.node_id!r} has no interface addresses")
    interfaces_by_domain = resolved.domain_interfaces(node.node_id)
    areas_by_domain = {
        areas.domain_id: areas for areas in resolved.instance_areas_by_node().get(node.node_id, ())
    }
    links = _wan_links(resolved, node)
    segments = _segment_facts(node)
    te_interfaces = {
        name
        for domain in domains
        if "traffic_engineering" in domain.capabilities
        for name in interfaces_by_domain[domain.domain_id]
    }
    routes_by_domain = {
        domain.domain_id: _boundary_static_routes(resolved, node, domain) for domain in domains
    }
    all_routes: list[dict[str, str]] = []
    for routes in routes_by_domain.values():
        for route in routes:
            if route not in all_routes:
                all_routes.append(route)
    carries_ipv6 = "ipv6" in node.address_families
    result: dict[str, Any] = {
        "hostname": node.node_id,
        "log_file": LOG_FILE,
        "system_id": _isis_system_id(resolved, node),
        # The families the session gives the node, in grammar order. A family
        # the node does not carry is neither addressed, enabled nor routed.
        "address_families": tuple(
            family for family in ADDRESS_FAMILIES if family in node.address_families
        ),
        "ipv4_loopback": _ip_from_interface(node.interfaces.lo0.ipv4, field="lo0.ipv4"),
        # An IPv6 loopback exists only where the session declares one.
        "ipv6_loopback": (
            _ip_from_interface(node.interfaces.lo0.ipv6, field="lo0.ipv6")
            if node.interfaces.lo0.ipv6 is not None
            else None
        ),
        "srgb_start": FRR_SRGB[0],
        "srgb_end": FRR_SRGB[1],
        "srlb_start": SRLB[0],
        "srlb_end": SRLB[1],
        "wan_interfaces": [
            {
                "name": name,
                "te": _te_link_params(node, name) if name in te_interfaces else None,
            }
            for name in links
        ],
        "static_links": [
            {
                "name": name,
                "peer_loopback_ipv4": _ip_from_interface(
                    link["peer"].interfaces.lo0.ipv4, field=f"{link['peer'].node_id}.lo0.ipv4"
                ),
                # Both ends carry IPv6 and the peer holds an IPv6 loopback.
                "peer_loopback_ipv6": (
                    _lo0_address(link["peer"], "ipv6") if carries_ipv6 else None
                ),
            }
            for name, link in links.items()
            if link["static_only"]
        ],
        "segment_interfaces": [
            {"name": name, "addresses": segment["addresses"]} for name, segment in segments.items()
        ],
        "boundary_static_routes": all_routes,
        "bfd_profiles": [
            {
                "name": domain.domain_id,
                "detect_multiplier": domain.timers.bfd.detect_multiplier,
                "rx_interval": domain.timers.bfd.rx_interval_ms,
                "tx_interval": domain.timers.bfd.tx_interval_ms,
            }
            for domain in domains
            if domain.timers.bfd.enabled
        ],
        "ldp_interfaces": sorted(
            {
                name
                for domain in domains
                if "mpls" in domain.capabilities and "segment_routing" not in domain.capabilities
                for name in interfaces_by_domain[domain.domain_id]
                if name in links
            }
        ),
        "domains": [
            _domain_facts(
                resolved,
                node,
                domain,
                areas=areas_by_domain.get(domain.domain_id),
                interfaces=interfaces_by_domain[domain.domain_id],
                links=links,
                segments=segments,
                routes=routes_by_domain[domain.domain_id],
                sid_by_domain=sid_by_domain,
            )
            for domain in domains
        ],
    }
    return result


def _domain_facts(
    resolved: ResolvedSession,
    node: ResolvedNode,
    domain: ResolvedRoutingDomain,
    *,
    areas: InstanceAreas | None,
    interfaces: tuple[str, ...],
    links: dict[str, dict[str, Any]],
    segments: dict[str, dict[str, Any]],
    routes: list[dict[str, str]],
    sid_by_domain: Mapping[str, Mapping[str, int]],
) -> dict[str, Any]:
    """One instance's part of the router: its instance and its interfaces.

    A fixed link in the instance runs the IGP at a metric from its own
    terminal's transmit rate; an access link at the access metric. Every
    area comes from the resolved session: an IS-IS router's area addresses,
    and the area of each OSPF interface and of the loopback. A segment joins
    the IGP in each family whose prefix the router originates, actively
    where another participant of the instance shares the segment and
    passively otherwise. The router originates its prefixes and default
    routes into every instance it participates in, and redistributes into
    an instance the boundary routes exported to it.
    """
    is_igp = domain.protocol in FRR_MAXIMUM_IGP_METRIC
    isis_areas = areas if isinstance(areas, IsisInstanceAreas) else None
    ospf_areas = areas if isinstance(areas, OspfInstanceAreas) else None
    if is_igp and areas is None:
        raise AdapterRenderRefusal(
            f"router {node.node_id!r} has no resolved areas in {domain.protocol} instance "
            f"{domain.domain_id!r}"
        )
    bfd_enabled = domain.timers.bfd.enabled
    sr_enabled = "segment_routing" in domain.capabilities
    node_sid_index = None
    if sr_enabled:
        domain_sids = sid_by_domain.get(domain.domain_id)
        node_sid_index = None if domain_sids is None else domain_sids.get(node.node_id)
        if node_sid_index is None:
            raise AdapterRenderRefusal(
                f"segment routing domain {domain.domain_id!r} has no resolved SID index "
                f"for {node.node_id!r}"
            )
    facts: dict[str, Any] = {
        "domain_id": domain.domain_id,
        "protocol": domain.protocol,
        "area_addresses": isis_areas.area_addresses if isis_areas is not None else (),
        "loopback_area": ospf_areas.loopback_area if ospf_areas is not None else None,
        "bfd_profile": domain.domain_id if bfd_enabled else None,
        "sr_enabled": sr_enabled,
        "te_enabled": "traffic_engineering" in domain.capabilities,
        "node_sid_index": node_sid_index,
        "default_route_families": _default_route_families(node),
        "default_route_metric": _DEFAULT_ROUTE_METRIC,
        "redistribute_static": (
            tuple(
                family
                for family in ADDRESS_FAMILIES
                if any(route["family"] == family for route in routes)
            )
            if is_igp
            else ()
        ),
        **_timer_template_facts(domain),
    }
    wan: list[dict[str, Any]] = []
    for name in interfaces:
        link = links.get(name)
        if link is None:
            continue
        entry: dict[str, Any] = {
            "name": name,
            "bfd": bfd_enabled,
            # IPv6 runs on an interface that reaches a peer carrying it.
            "ipv6": "ipv6" in node.address_families and link["reaches_ipv6"],
        }
        if is_igp:
            entry["metric"] = (
                _ACCESS_LINK_METRIC if link["access"] else _igp_metric(node, name, domain.protocol)
            )
        if ospf_areas is not None:
            entry["ospf_area"] = ospf_areas.interface_areas[name]
        wan.append(entry)
    facts["interfaces"] = wan
    facts["segments"] = [
        {
            "name": name,
            "ospf_area": ospf_areas.interface_areas[name] if ospf_areas is not None else None,
            "metric": _SEGMENT_METRIC,
            "igp_families": segments[name]["igp_families"],
            "igp_active": _segment_has_participant(resolved, node, domain, name),
            "bfd": bfd_enabled
            and bool(segments[name]["igp_families"])
            and _segment_has_participant(resolved, node, domain, name),
        }
        for name in interfaces
        if name in segments
    ]
    return facts


def _igp_metric(node: ResolvedNode, interface: str, protocol: str) -> int:
    """A fixed link's IGP metric from its own terminal's transmit rate.

    A metric above what the protocol accepts is refused rather than clipped.
    """
    transmit = node.wan_terminal(interface).transmit_mbps
    metric = max(_MINIMUM_IGP_METRIC, int(FRR_REFERENCE_BANDWIDTH_MBPS / transmit))
    maximum = FRR_MAXIMUM_IGP_METRIC[protocol]
    if metric > maximum:
        raise AdapterRenderRefusal(
            f"node {node.node_id!r} interface {interface!r} transmits {transmit} Mb/s, "
            f"so its {protocol} metric {metric} exceeds the protocol maximum {maximum}"
        )
    return metric


def _te_link_params(node: ResolvedNode, interface: str) -> dict[str, str]:
    """MPLS-TE bandwidth for one WAN interface, from its own terminal.

    Maximum bandwidth is the terminal's transmit rate. FRR takes bytes per
    second.
    """
    transmit_mbps = node.wan_terminal(interface).transmit_mbps
    max_bw = transmit_mbps * 1_000_000 / 8
    return {
        "max_bw": f"{max_bw:g}",
        "reservable_bw": f"{max_bw * _TE_RESERVABLE_FRACTION:g}",
    }


def _wan_links(resolved: ResolvedSession, node: ResolvedNode) -> dict[str, dict[str, Any]]:
    """Every WAN interface the node's FRR configuration names, once each.

    Fixed links come from the node's non-access link candidates, in candidate
    order, each with its peer; ``static_only`` marks a fixed link a
    ``static_ip`` routing boundary crosses. Access links follow, with no
    fixed peer. ``reaches_ipv6`` marks an interface some possible peer of
    which carries IPv6.
    """
    static_rules = _static_boundary_rule_ids(resolved)
    carries_ipv6 = {other.node_id for other in resolved.nodes if "ipv6" in other.address_families}
    peers = resolved.wan_interface_peers()
    links: dict[str, dict[str, Any]] = {}
    for candidate in resolved.link_candidates:
        if candidate.kind == "access" or node.node_id not in (candidate.node_a, candidate.node_b):
            continue
        side = 0 if candidate.node_a == node.node_id else 1
        name = candidate.fixed_interfaces[side]
        peer_id = candidate.node_b if side == 0 else candidate.node_a
        peer = resolved.node_by_id(peer_id)
        if peer is None or peer.interfaces is None:
            raise AdapterRenderRefusal(
                f"link candidate {candidate.rule_id!r} references unresolved peer {peer_id!r}"
            )
        if name in links:
            raise AdapterRenderRefusal(f"node {node.node_id!r} names WAN interface {name!r} twice")
        links[name] = {
            "peer": peer,
            "static_only": candidate.rule_id in static_rules,
            "access": False,
            "reaches_ipv6": peer_id in carries_ipv6,
        }
    for name in node.access_interfaces:
        if name in links:
            raise AdapterRenderRefusal(
                f"node {node.node_id!r} names WAN interface {name!r} as both a fixed "
                "link and an access link"
            )
        links[name] = {
            "peer": None,
            "static_only": False,
            "access": True,
            "reaches_ipv6": bool(peers.get((node.node_id, name), frozenset()) & carries_ipv6),
        }
    return links


def _segment_has_participant(
    resolved: ResolvedSession,
    node: ResolvedNode,
    domain: ResolvedRoutingDomain,
    interface: str,
) -> bool:
    """Whether another participant of ``domain`` shares this interface's segment."""
    for segment in resolved.ethernet_segments:
        if not any(
            member.node_id == node.node_id and member.interface == interface
            for member in segment.members
        ):
            continue
        return any(
            member.node_id != node.node_id and member.node_id in domain.node_ids
            for member in segment.members
        )
    return False


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
    prefixes. Literal prefix lists pass through split by family.
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
            for family in ADDRESS_FAMILIES:
                for prefix in getattr(node.originated_prefixes, family) or ():
                    if prefix not in seen:
                        seen.add(prefix)
                        prefixes[family].append(prefix)
    if export.export_node_loopbacks:
        for node_id in from_domain.node_ids:
            node = resolved.node_by_id(node_id)
            if node is None:
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
    installable only when this node carries it and, for a peer-loopback
    next hop, the peer holds a loopback of that family; the aggregate
    semantics are per installable family. Literal prefixes of a family that
    is not installable are an authoring error and refuse.
    """
    routes: list[dict[str, str]] = []
    emitted: set[tuple[str, str]] = set()
    domains_by_id = {item.domain_id: item for item in resolved.routing_domains}
    for item in resolved.boundary_imports():
        if item.node_id != node.node_id or item.export.to != domain.domain_id:
            continue
        boundary, export, peer_id = item.boundary, item.export, item.peer_id
        from_domain = domains_by_id[export.from_]
        peer = resolved.node_by_id(peer_id)
        if peer is None:
            raise AdapterRenderRefusal(f"boundary candidate references unresolved peer {peer_id!r}")
        exports = _boundary_export_prefixes(export, from_domain, resolved)
        for family in ADDRESS_FAMILIES:
            if family not in node.address_families:
                if isinstance(export.prefixes, tuple) and exports[family]:
                    raise AdapterRenderRefusal(
                        f"boundary over {boundary.over!r} exports {family} prefixes "
                        f"to {node.node_id!r}, which carries no {family}"
                    )
                continue
            if export.install_via is None or export.install_via == "peer_loopback":
                via = _lo0_address(peer, family)
                if via is None:
                    # Family not installable over this peer; the
                    # aggregate is defined per installable family.
                    # Literal prefixes of an uninstallable family are
                    # an authoring error — fail loud.
                    if isinstance(export.prefixes, tuple) and exports[family]:
                        raise AdapterRenderRefusal(
                            f"boundary over {boundary.over!r} exports {family} "
                            f"prefixes but peer {peer_id!r} has no {family} "
                            "loopback for install_via: peer_loopback"
                        )
                    continue
            else:
                via = export.install_via
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


def _segment_facts(node: ResolvedNode) -> dict[str, dict[str, Any]]:
    """The router's Ethernet segment interfaces: addresses and IGP families.

    A segment joins the IGP in each family whose allocated prefix the node
    originates. An originated prefix that no segment of the node carries is
    refused: FRR renders connected segment prefixes and default routes.
    """
    if node.interfaces is None or not node.interfaces.ethernet:
        return {}
    originated: dict[str, set[str]] = {family: set() for family in ADDRESS_FAMILIES}
    if node.originated_prefixes is not None:
        for family in ADDRESS_FAMILIES:
            for prefix in getattr(node.originated_prefixes, family) or ():
                network = ipaddress.ip_network(prefix, strict=False)
                if network.prefixlen != 0:
                    originated[family].add(str(network))
    facts: dict[str, dict[str, Any]] = {}
    covered: set[tuple[str, str]] = set()
    for name, segment in sorted(node.interfaces.ethernet.items()):
        addresses: list[dict[str, str]] = []
        igp_families: list[str] = []
        for family in ADDRESS_FAMILIES:
            value = getattr(segment, family)
            if value is None:
                continue
            addresses.append({"family": family, "address": value})
            network = str(ipaddress.ip_interface(value).network)
            if network in originated[family]:
                igp_families.append(family)
                covered.add((family, network))
        facts[name] = {"addresses": addresses, "igp_families": tuple(igp_families)}
    uncovered = sorted(
        network
        for family, networks in originated.items()
        for network in networks
        if (family, network) not in covered
    )
    if uncovered:
        raise AdapterRenderRefusal(
            f"node {node.node_id!r} originates non-connected prefix(es) {uncovered}; "
            "FRR rendering supports connected segment prefixes and default routes"
        )
    return facts


def _default_route_families(node: ResolvedNode) -> tuple[str, ...]:
    """The families in which the node originates a default route."""
    if node.originated_prefixes is None:
        return ()
    return tuple(
        family
        for family in ADDRESS_FAMILIES
        if any(
            ipaddress.ip_network(prefix, strict=False).prefixlen == 0
            for prefix in getattr(node.originated_prefixes, family) or ()
        )
    )


def _ip_from_interface(value: str | None, *, field: str) -> str:
    if value is None:
        raise AdapterRenderRefusal(f"required interface address is missing: {field}")
    return str(ipaddress.ip_interface(value).ip)
