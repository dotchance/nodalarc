"""Tests for the measurement service's RoutingStackConfig model."""

from nodalarc.models.routing_stack import RoutingStackConfig


def test_defaults_when_fields_omitted():
    """Fields should default correctly when not specified in YAML."""
    cfg = RoutingStackConfig(
        name="test",
        image="test:latest",
        config_templates=[],
    )
    assert cfg.segment_routing is False
    assert cfg.ttl_propagation is None
