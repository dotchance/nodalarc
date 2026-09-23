from pathlib import Path

import yaml
from nodalarc.catalog_closure import FilesystemCatalogReadView
from nodalarc.catalog_paths import CatalogRoots
from nodalarc.models.resolved_session import SourceContext
from nodalarc.resolve_session import resolve_session_with_assets
from vs_api.resolved_runtime_views import routing_label, tracer_node_registry
from vs_api.session_context import SessionContext


def _resolution():
    path = Path("catalog/nodalarc/sessions/earth-leo-simple.yaml")
    return resolve_session_with_assets(
        yaml.safe_load(path.read_text(encoding="utf-8")),
        catalog=FilesystemCatalogReadView(CatalogRoots.from_catalog_root("catalog/nodalarc")),
        source_context=SourceContext(origin="test.vs-api-resolved-view"),
    )


def test_session_context_accepts_authoritative_resolution_without_session_file():
    resolution = _resolution()

    context = SessionContext(
        "run-test-resolved-0001",
        resolution=resolution,
        source_id="user:sessions/resolved-test.yaml",
        history_path=None,
    )

    assert context.session_file == ""
    assert context.session_source_id == "user:sessions/resolved-test.yaml"
    assert context.session_resolution is resolution
    assert context.constellation_name == resolution.resolved.session.name


def test_tracer_view_uses_resolved_loopbacks_interfaces_and_sid_indices():
    resolution = _resolution()

    registry = tracer_node_registry(resolution)

    assert set(registry) == set(resolution.resolved.node_ids())
    for node_id, tracer_node in registry.items():
        resolved = resolution.resolved.node_by_id(node_id)
        assert tracer_node.node_type == resolved.kind
        assert "/" not in tracer_node.loopback_ipv4
        assert tracer_node.addresses_ipv4[0] == tracer_node.loopback_ipv4
        assert tracer_node.addresses_ipv4[1:] == tuple(
            address.ipv4.split("/")[0]
            for address in resolved.interfaces.ethernet.values()
            if address.ipv4 is not None
        )
        assert tracer_node.sid == resolution.resolved.sid_index_by_node_id().get(node_id)


def test_tracer_view_names_the_gateway_for_host_nodes():
    """TEMPORARY host-node trace stopgap: a host node carries its derived FRR
    gateway so the tracer can run the trace from there; routed nodes carry
    None."""
    path = Path("catalog/nodalarc/sessions/earth-luna-quic.yaml")
    resolution = resolve_session_with_assets(
        yaml.safe_load(path.read_text(encoding="utf-8")),
        catalog=FilesystemCatalogReadView(CatalogRoots.from_catalog_root("catalog/nodalarc")),
        source_context=SourceContext(origin="test.vs-api-host-trace"),
    )
    registry = tracer_node_registry(resolution)

    hosts = [n for n in resolution.resolved.nodes if n.forwarding == "host"]
    assert hosts
    for host in hosts:
        tracer_node = registry[host.node_id]
        assert host.host_attachment is not None
        assert tracer_node.trace_gateway_node_id == host.host_attachment.gateway_node_id
    # A routed node carries no trace gateway.
    routed = next(n for n in resolution.resolved.nodes if n.forwarding == "routed")
    assert registry[routed.node_id].trace_gateway_node_id is None


def test_a_session_without_authored_routing_reports_the_domain_it_runs():
    """earth-leo-simple has no routing section; its routers run the resolver's
    default IS-IS domain, and VS-API says so."""
    resolution = _resolution()
    resolved = resolution.resolved
    assert resolved.routing is None

    context = SessionContext(
        "run-test-resolved-0001",
        resolution=resolution,
        source_id="nodalarc:sessions/earth-leo-simple.yaml",
        history_path=None,
    )

    assert routing_label(resolved) == "default_domain:isis"
    assert context.routing_stack == "default_domain:isis"
    [domain] = resolved.routing_domains
    areas = resolved.routing_area_by_node_id()
    assert set(areas) == set(domain.node_ids)
    assert set(areas.values()) == {"49.0001"}
    ground = next(n for n in resolved.nodes if n.kind == "ground_station")
    assert context.nodes[ground.node_id].routing_area == "49.0001"
