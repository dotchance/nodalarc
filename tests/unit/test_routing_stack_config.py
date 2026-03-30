"""Tests for ResolvedStack — segment_routing, sysctls, SID derivation."""

from nodalarc.models.routing_stack import RoutingStackConfig
from nodalarc.stack_resolver import resolve_stack


def test_isis_sr_has_segment_routing():
    resolved = resolve_stack("isis", ["sr"])
    assert resolved.segment_routing is True
    assert resolved.ttl_propagation == "pipe"
    assert resolved.sysctls["net.mpls.ip_ttl_propagate"] == "0"


def test_ospf_te_no_segment_routing():
    resolved = resolve_stack("ospf", ["te"])
    assert resolved.segment_routing is False


def test_isis_plain_no_segment_routing():
    resolved = resolve_stack("isis", [])
    assert resolved.segment_routing is False


def test_defaults_when_fields_omitted():
    """Fields should default correctly when not specified in YAML."""
    cfg = RoutingStackConfig(
        name="test",
        image="test:latest",
        config_templates=[],
    )
    assert cfg.segment_routing is False
    assert cfg.ttl_propagation is None
