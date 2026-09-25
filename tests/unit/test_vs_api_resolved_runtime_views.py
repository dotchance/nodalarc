from pathlib import Path

import yaml
from nodalarc.catalog_closure import FilesystemCatalogReadView
from nodalarc.catalog_paths import CatalogRoots
from nodalarc.models.resolved_session import IsisInstanceAreas, SourceContext
from nodalarc.models.vs_api import NodeInstanceInterface, NodeRoutingInstance
from nodalarc.resolve_session import resolve_session_with_assets
from vs_api.resolved_runtime_views import (
    routing_instances_by_node_id,
    routing_label,
    tracer_node_registry,
)
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


def test_tracer_view_uses_resolved_loopbacks_and_interface_addresses():
    resolution = _resolution()

    registry = tracer_node_registry(resolution)

    assert set(registry) == set(resolution.resolved.node_ids())
    for node_id, tracer_node in registry.items():
        resolved = resolution.resolved.node_by_id(node_id)
        assert "/" not in tracer_node.loopback_ipv4
        assert tracer_node.addresses_ipv4[0] == tracer_node.loopback_ipv4
        assert tracer_node.addresses_ipv4[1:] == tuple(
            address.ipv4.split("/")[0]
            for address in resolved.interfaces.ethernet.values()
            if address.ipv4 is not None
        )


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
    areas = resolved.instance_areas_by_node()
    assert set(areas) == set(domain.node_ids)
    assert set(areas.values()) == {(IsisInstanceAreas("default_domain", ("49.0001",)),)}
    ground = next(n for n in resolved.nodes if n.kind == "ground_station")
    state = context.nodes[ground.node_id]
    assert state.role == "router"
    assert state.routing_instances == (
        NodeRoutingInstance(
            domain_id="default_domain",
            protocol="isis",
            areas=("49.0001",),
            interfaces=tuple(
                NodeInstanceInterface(name=name, area_id=None)
                for name in resolved.domain_interfaces(ground.node_id)["default_domain"]
            ),
            area_border=False,
            as_boundary=False,
        ),
    )


def test_a_router_in_two_instances_reports_each_instance_with_its_own_areas():
    path = Path("catalog/nodalarc/sessions/earth-leo-simple.yaml")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["routing"] = {
        "domains": [
            {
                "id": "orbital",
                "protocol": "isis",
                "selectors": [{"any": [{"segment": "leo"}, {"segment": "ground"}]}],
            },
            {"id": "terrestrial", "protocol": "ospf", "selectors": [{"segment": "ground"}]},
        ]
    }
    resolution = resolve_session_with_assets(
        raw,
        catalog=FilesystemCatalogReadView(CatalogRoots.from_catalog_root("catalog/nodalarc")),
        source_context=SourceContext(origin="test.vs-api-two-domains"),
    )
    context = SessionContext(
        "run-test-resolved-0002",
        resolution=resolution,
        source_id="user:sessions/two-domains.yaml",
        history_path=None,
    )
    ground = next(n for n in resolution.resolved.nodes if n.kind == "ground_station")
    lan = tuple(sorted(ground.interfaces.ethernet))

    [isis, ospf] = context.nodes[ground.node_id].routing_instances
    assert (isis.domain_id, isis.protocol, isis.areas) == ("orbital", "isis", ("49.0001",))
    assert all(interface.area_id is None for interface in isis.interfaces)
    assert (ospf.domain_id, ospf.protocol, ospf.areas) == ("terrestrial", "ospf", ("0.0.0.0",))
    assert ospf.interfaces == tuple(
        NodeInstanceInterface(name=name, area_id="0.0.0.0") for name in lan
    )
    assert not (isis.area_border or ospf.area_border or isis.as_boundary or ospf.as_boundary)


def test_the_luna_boundary_relays_are_reported_as_as_boundary_routers():
    path = Path("catalog/nodalarc/sessions/earth-leo-heo-geo-luna-reachability.yaml")
    resolution = resolve_session_with_assets(
        yaml.safe_load(path.read_text(encoding="utf-8")),
        catalog=FilesystemCatalogReadView(CatalogRoots.from_catalog_root("catalog/nodalarc")),
        source_context=SourceContext(origin="test.vs-api-asbr"),
    )
    instances = {
        node_id: {instance.domain_id: instance for instance in items}
        for node_id, items in routing_instances_by_node_id(resolution.resolved).items()
    }

    assert instances["luna-relay-sat-p00s00"]["luna_domain"].as_boundary
    assert instances["geo-relay-sat-p00s03"]["earth_domain"].as_boundary
    # One flat area per instance: no area border routers.
    assert not any(
        instance.area_border for items in instances.values() for instance in items.values()
    )
    assert instances["luna-relay-sat-p00s00"]["luna_domain"].areas == ("49.0001",)
    assert instances["geo-relay-sat-p00s03"]["earth_domain"].areas == ("49.0001",)
