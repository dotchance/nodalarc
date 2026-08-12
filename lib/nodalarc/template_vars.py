# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Template variable builder for catalog-resolved runtime sessions."""

from __future__ import annotations

import ipaddress
from typing import Any

from nodalarc.models.resolved_session import ResolvedNode, ResolvedRoutingDomain, ResolvedSession


def build_template_vars_from_resolved(
    resolved: ResolvedSession,
    node_id: str,
    *,
    stack_variables: dict[str, Any] | None = None,
    node_sid_index: int | None = None,
) -> dict[str, Any]:
    """Build FRR template variables from the catalog-resolved runtime view."""
    node = resolved.node_by_id(node_id)
    if node is None:
        raise ValueError(f"unknown resolved node_id {node_id!r}")
    if node.interfaces is None:
        raise ValueError(f"resolved node {node_id!r} has no interface addresses")
    if resolved.time is None:
        raise ValueError("resolved session is missing time configuration for FRR rendering")
    domain = _single_routing_domain_for_node(resolved, node)
    result: dict[str, Any] = dict(stack_variables or {})
    if result.get("sr_enabled"):
        if node_sid_index is None:
            raise ValueError(
                f"segment routing is enabled but no resolved SID index was provided for {node_id}"
            )
        result["node_sid_index"] = node_sid_index

    result.update(_timer_template_facts(domain))
    segment_interfaces, default_route, default_metric = _segment_template_facts(
        resolved, node, domain
    )
    result["segment_interfaces"] = segment_interfaces
    result["default_route_originate"] = default_route
    result["default_route_metric"] = default_metric
    result.update(
        {
            "node_id": node.node_id,
            "hostname": node.node_id,
            "node_type": "satellite" if node.kind == "satellite" else "ground_station",
            "area_id": _resolved_area_id(domain, node),
            "system_id": _isis_system_id(resolved, node),
            "mgmt_interface": "eth0",
            "compression_factor": resolved.time.compression,
            "protocol": domain.protocol,
            "ipv4_loopback": _ip_from_interface(node.interfaces.lo0.ipv4, field="lo0.ipv4"),
            # v4-only nodes are a legitimate resolved shape; templates render
            # the v6 loopback only when the resolver assigned one.
            "ipv6_loopback": (
                _ip_from_interface(node.interfaces.lo0.ipv6, field="lo0.ipv6")
                if node.interfaces.lo0.ipv6 is not None
                else None
            ),
            "interface_info": _resolved_interface_info(resolved, node, domain),
            "neighbors": _resolved_neighbors(resolved, node),
        }
    )
    boundary_routes = _boundary_static_routes(resolved, node, domain)
    result["boundary_static_routes"] = boundary_routes
    # Border nodes must redistribute boundary statics into their IGP, or the
    # rest of the domain never learns the exported reachability.
    result["redistribute_static"] = bool(boundary_routes) and domain.protocol in {"isis", "ospf"}

    if node.kind == "satellite":
        gnd_interfaces = [
            iface.name for iface in node.wan_interfaces if iface.name.startswith("gnd")
        ]
        isl_interfaces = [
            iface.name for iface in node.wan_interfaces if iface.name.startswith("isl")
        ]
        result.update(
            {
                "gnd_interfaces": gnd_interfaces,
                "isl_interfaces": isl_interfaces,
                "isl_count": len(isl_interfaces),
            }
        )
        # Grid coordinates exist only for grid-born satellites (walker/
        # ring planes). Individually placed satellites — GEO longitude
        # slots, raw state vectors — legitimately have neither: no FRR
        # template consumes them, identity never derives from them
        # (system_id uses the resolver node index), and pod placement
        # defaults an absent plane to bucket 0. Emitting them only when
        # present keeps every consumer on its documented absent-key path.
        if node.plane is not None and node.slot is not None:
            result.update({"plane": node.plane, "slot": node.slot})
        return result

    if node.kind != "ground_station":
        raise ValueError(f"unsupported resolved node kind for FRR rendering: {node.kind!r}")
    result.update(
        {
            "gs_name": node.local_node_id,
            "gnd_interfaces": [iface.name for iface in node.wan_interfaces],
            "isl_interfaces": [],
            "isl_count": 0,
        }
    )
    return result


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
        "bfd_enabled": timers.bfd.enabled,
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
    """
    index = resolved.node_index_by_node_id()[node.node_id]
    return f"0000.{(index >> 16) & 0xFFFF:04x}.{index & 0xFFFF:04x}"


def _single_routing_domain_for_node(
    resolved: ResolvedSession,
    node: ResolvedNode,
) -> ResolvedRoutingDomain:
    domains = [domain for domain in resolved.routing_domains if node.node_id in domain.node_ids]
    if len(domains) != 1:
        raise ValueError(
            f"node {node.node_id!r} must resolve to exactly one routing domain for current "
            f"FRR rendering; got {[domain.domain_id for domain in domains]}"
        )
    return domains[0]


def _resolved_area_id(domain: ResolvedRoutingDomain, node: ResolvedNode) -> str:
    assignment = domain.area_assignment
    is_ospf = domain.protocol == "ospf"
    default = "0.0.0.0" if is_ospf else "49.0001"
    if assignment is None or assignment.strategy == "flat":
        return (
            assignment.gs_area_id if assignment is not None and assignment.gs_area_id else default
        )
    if node.kind != "satellite":
        if assignment.strategy == "explicit":
            matches = [
                mapping.area_id
                for mapping in assignment.assignments or ()
                if mapping.ground_stations == "all"
                or (
                    isinstance(mapping.ground_stations, tuple)
                    and node.local_node_id in mapping.ground_stations
                )
            ]
            if len(matches) > 1:
                raise ValueError(
                    f"explicit area assignment in domain {domain.domain_id!r} maps ground "
                    f"station {node.local_node_id!r} more than once"
                )
            if matches:
                return matches[0]
        return assignment.gs_area_id or default
    if node.plane is None:
        raise ValueError(f"node {node.node_id!r} is missing plane for area assignment")
    if assignment.strategy == "per_plane":
        return f"0.0.0.{node.plane + 1}" if is_ospf else f"49.{node.plane + 1:04d}"
    if assignment.strategy == "stripe":
        if assignment.planes_per_stripe is None:
            raise ValueError("stripe area assignment requires planes_per_stripe")
        stripe_index = node.plane // assignment.planes_per_stripe
        return f"0.0.0.{stripe_index + 1}" if is_ospf else f"49.{stripe_index + 1:04d}"
    if assignment.strategy == "explicit":
        for mapping in assignment.assignments or ():
            if mapping.planes is not None and node.plane in mapping.planes:
                return mapping.area_id
        raise ValueError(
            f"explicit area assignment in domain {domain.domain_id!r} has no plane mapping "
            f"for node {node.node_id!r}"
        )
    raise ValueError(f"unsupported area assignment strategy {assignment.strategy!r}")


def _resolved_interface_info(
    resolved: ResolvedSession,
    node: ResolvedNode,
    domain: ResolvedRoutingDomain,
) -> dict[str, dict[str, Any]]:
    static_rules = _static_boundary_rule_ids(resolved)
    info: dict[str, dict[str, Any]] = {}
    for candidate in resolved.link_candidates:
        if candidate.kind == "access":
            continue
        interface_a, interface_b = candidate.fixed_interfaces
        if candidate.node_a == node.node_id:
            iface = interface_a
            peer_id = candidate.node_b
        elif candidate.node_b == node.node_id:
            iface = interface_b
            peer_id = candidate.node_a
        else:
            continue
        peer = resolved.node_by_id(peer_id)
        if peer is None or peer.interfaces is None:
            raise ValueError(
                f"link candidate {candidate.rule_id!r} references unresolved peer {peer_id!r}"
            )
        peer_domain = _single_routing_domain_for_node(resolved, peer)
        node_area = _resolved_area_id(domain, node)
        peer_area = _resolved_area_id(peer_domain, peer)
        static_only = candidate.rule_id in static_rules
        info[iface] = {
            "peer_node_id": peer_id,
            "link_type": f"{'static_ip' if static_only else 'link_rule'}:{candidate.rule_id}",
            "priority": candidate.priority,
            "peer_area_id": peer_area,
            "cross_area": domain.domain_id != peer_domain.domain_id or node_area != peer_area,
            "bandwidth_mbps": float(candidate.bandwidth_mbps),
            "static_only": static_only,
            "peer_loopback_ipv4": _ip_from_interface(
                peer.interfaces.lo0.ipv4,
                field=f"{peer.node_id}.lo0.ipv4",
            ),
        }
    return info


def _resolved_neighbors(resolved: ResolvedSession, node: ResolvedNode) -> dict[str, str]:
    neighbors: dict[str, str] = {}
    for candidate in resolved.link_candidates:
        if candidate.kind == "access":
            continue
        interface_a, interface_b = candidate.fixed_interfaces
        if candidate.node_a == node.node_id:
            neighbors[interface_a] = candidate.node_b
        elif candidate.node_b == node.node_id:
            neighbors[interface_b] = candidate.node_a
    return neighbors


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
    passive otherwise — the honest stub posture.
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
        covered.update(networks & originated_networks)
        interfaces.append(
            {
                "name": name,
                "addresses": addresses,
                "metric": 10,
                "igp_enabled": igp_enabled,
                "igp_active": _segment_peer_count(resolved, node, domain, name) >= 1,
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
