"""VS-API runtime views derived from the authoritative resolved session."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from nodalarc.models.resolved_session import IsisInstanceAreas, OspfInstanceAreas, ResolvedSession
from nodalarc.models.vs_api import NodeInstanceInterface, NodeRoutingInstance
from nodalarc.resolve_session import SessionResolution


@dataclass(frozen=True, slots=True)
class TracerNode:
    node_id: str
    loopback_ipv4: str
    # Every IPv4 address the resolver assigned the node: its loopback, then
    # its numbered Ethernet interfaces. A traceroute hop answering from any of
    # them is this node.
    addresses_ipv4: tuple[str, ...]
    # TEMPORARY (host-node trace stopgap): a host-forwarding node runs no
    # routing daemon and its container carries no trace tooling, so it cannot
    # be a real trace endpoint. Until there is a proper substrate-truth path
    # view for LAN-attached application nodes, this names the FRR gateway the
    # host attaches to; the tracer runs the trace from that gateway instead.
    # This is NOT the real path (it omits the host<->gateway LAN hop) and
    # must be replaced with an honest host-aware trace. See continuous_tracer.
    trace_gateway_node_id: str | None = None


def routing_label(resolved: ResolvedSession) -> str:
    """The compact label of the routing domains the session runs.

    Read from the resolved runtime domains: a session with no authored routing
    section still runs the resolver's default domain over its routers.
    """
    if not resolved.routing_domains:
        return "unrouted"
    return " + ".join(
        f"{domain.domain_id}:{domain.protocol}" for domain in resolved.routing_domains
    )


def constellation_label(resolved: ResolvedSession) -> str:
    """Return the compact satellite-segment label used by session listings."""
    segments = sorted({node.segment_id for node in resolved.nodes if node.kind == "satellite"})
    return " + ".join(segments) if segments else "none"


def tracer_node_registry(resolution: SessionResolution) -> dict[str, TracerNode]:
    """Build the path-tracing node view without reparsing session YAML."""
    if not isinstance(resolution, SessionResolution):
        raise TypeError("resolution must be a SessionResolution")
    resolved = resolution.resolved
    nodes: dict[str, TracerNode] = {}
    for node in resolved.nodes:
        if node.interfaces is None or node.interfaces.lo0.ipv4 is None:
            continue
        loopback = str(ipaddress.ip_interface(node.interfaces.lo0.ipv4).ip)
        ethernet = tuple(
            str(ipaddress.ip_interface(address.ipv4).ip)
            for address in node.interfaces.ethernet.values()
            if address.ipv4 is not None
        )
        # TEMPORARY: a host node's derived attachment names its FRR gateway;
        # the tracer substitutes it because the host itself cannot be traced.
        gateway = node.host_attachment.gateway_node_id if node.host_attachment else None
        nodes[node.node_id] = TracerNode(
            node_id=node.node_id,
            loopback_ipv4=loopback,
            addresses_ipv4=(loopback, *ethernet),
            trace_gateway_node_id=gateway,
        )
    return nodes


def routing_instances_by_node_id(
    resolved: ResolvedSession,
) -> dict[str, tuple[NodeRoutingInstance, ...]]:
    """Every participant's routing instances, from the resolved session's facts.

    A node that participates in no instance is absent.
    """
    interfaces_by_node = resolved.domain_interfaces_by_node()
    areas_by_node = {
        (node_id, areas.domain_id): areas
        for node_id, node_areas in resolved.instance_areas_by_node().items()
        for areas in node_areas
    }
    area_border = {
        (node_id, domain_id)
        for node_id, domain_ids in resolved.area_border_instances_by_node().items()
        for domain_id in domain_ids
    }
    as_boundary = {
        (node_id, domain_id)
        for node_id, domain_ids in resolved.as_boundary_instances_by_node().items()
        for domain_id in domain_ids
    }
    instances: dict[str, list[NodeRoutingInstance]] = {}
    for domain in resolved.routing_domains:
        for node_id in domain.node_ids:
            key = (node_id, domain.domain_id)
            areas = areas_by_node.get(key)
            names = interfaces_by_node[node_id][domain.domain_id]
            if isinstance(areas, OspfInstanceAreas):
                area_list = areas.areas
                interfaces = tuple(
                    NodeInstanceInterface(name=name, area_id=areas.interface_areas[name])
                    for name in names
                )
            else:
                area_list = areas.area_addresses if isinstance(areas, IsisInstanceAreas) else ()
                interfaces = tuple(NodeInstanceInterface(name=name, area_id=None) for name in names)
            instances.setdefault(node_id, []).append(
                NodeRoutingInstance(
                    domain_id=domain.domain_id,
                    protocol=domain.protocol,
                    areas=area_list,
                    interfaces=interfaces,
                    area_border=key in area_border,
                    as_boundary=key in as_boundary,
                )
            )
    return {node_id: tuple(items) for node_id, items in instances.items()}
