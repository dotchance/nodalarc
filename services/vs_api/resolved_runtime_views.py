"""VS-API runtime views derived from the authoritative resolved session."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from nodalarc.models.resolved_session import ResolvedSession
from nodalarc.resolve_session import SessionResolution


@dataclass(frozen=True, slots=True)
class TracerNode:
    node_id: str
    node_type: str
    sid: int | None
    loopback_ipv4: str
    # TEMPORARY (host-node trace stopgap): a host-forwarding node runs no
    # routing daemon and its container carries no trace tooling, so it cannot
    # be a real trace endpoint. Until there is a proper substrate-truth path
    # view for LAN-attached application nodes, this names the FRR gateway the
    # host attaches to; the tracer runs the trace from that gateway instead.
    # This is NOT the real path (it omits the host<->gateway LAN hop) and
    # must be replaced with an honest host-aware trace. See continuous_tracer.
    trace_gateway_node_id: str | None = None


def routing_label(resolved: ResolvedSession) -> str:
    """Return the compact routing-domain label used by VS-API views."""
    routing = resolved.routing
    if routing is None or not routing.domains:
        return "unrouted"
    return " + ".join(f"{domain.id}:{domain.protocol}" for domain in routing.domains)


def constellation_label(resolved: ResolvedSession) -> str:
    """Return the compact satellite-segment label used by session listings."""
    segments = sorted({node.segment_id for node in resolved.nodes if node.kind == "satellite"})
    return " + ".join(segments) if segments else "none"


def tracer_node_registry(resolution: SessionResolution) -> dict[str, TracerNode]:
    """Build the path-tracing node view without reparsing session YAML."""
    if not isinstance(resolution, SessionResolution):
        raise TypeError("resolution must be a SessionResolution")
    resolved = resolution.resolved
    sid_by_node = resolved.sid_index_by_node_id()
    nodes: dict[str, TracerNode] = {}
    for node in resolved.nodes:
        if node.interfaces is None or node.interfaces.lo0.ipv4 is None:
            continue
        loopback = str(ipaddress.ip_interface(node.interfaces.lo0.ipv4).ip)
        # TEMPORARY: a host node's derived attachment names its FRR gateway;
        # the tracer substitutes it because the host itself cannot be traced.
        gateway = node.host_attachment.gateway_node_id if node.host_attachment else None
        nodes[node.node_id] = TracerNode(
            node_id=node.node_id,
            node_type=node.kind,
            sid=sid_by_node.get(node.node_id),
            loopback_ipv4=loopback,
            trace_gateway_node_id=gateway,
        )
    return nodes
