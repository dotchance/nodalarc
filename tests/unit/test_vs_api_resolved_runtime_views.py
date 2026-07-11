from pathlib import Path

import yaml
from nodalarc.catalog_paths import CatalogRoots
from nodalarc.models.resolved_session import SourceContext
from nodalarc.resolve_session import resolve_session_with_assets
from vs_api.resolved_runtime_views import tracer_node_registry
from vs_api.session_context import SessionContext


def _resolution():
    path = Path("catalog/nodalarc/sessions/earth-leo-simple.yaml")
    return resolve_session_with_assets(
        yaml.safe_load(path.read_text(encoding="utf-8")),
        catalog_roots=CatalogRoots.from_catalog_root("catalog/nodalarc"),
        source_context=SourceContext(origin="test.vs-api-resolved-view"),
    )


def test_session_context_accepts_authoritative_resolution_without_session_file():
    resolution = _resolution()

    context = SessionContext(
        "run-test-resolved-0001",
        resolution=resolution,
        source_id="user:sessions/resolved-test.yaml",
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
        assert tracer_node.sid == resolution.resolved.sid_index_by_node_id().get(node_id)
