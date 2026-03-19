"""Tests for RoutingStackConfig — segment_routing and ttl_propagation fields."""

from pathlib import Path

import yaml
from nodalarc.models.routing_stack import RoutingStackConfig


def _load_stack(name: str) -> RoutingStackConfig:
    stack_dir = Path("configs/routing-stacks") / name
    raw = yaml.safe_load((stack_dir / "stack.yaml").read_text())
    return RoutingStackConfig.model_validate(raw["stack"])


def test_frr_isis_sr_has_segment_routing():
    cfg = _load_stack("frr-isis-sr")
    assert cfg.segment_routing is True
    assert cfg.ttl_propagation == "uniform"


def test_frr_static_sr_has_segment_routing():
    cfg = _load_stack("frr-static-sr")
    assert cfg.segment_routing is True
    assert cfg.ttl_propagation == "uniform"


def test_frr_ospf_te_no_segment_routing():
    cfg = _load_stack("frr-ospf-te")
    assert cfg.segment_routing is False
    assert cfg.ttl_propagation is None


def test_nodalpath_fwd_no_segment_routing():
    cfg = _load_stack("nodalpath-fwd")
    assert cfg.segment_routing is False
    assert cfg.ttl_propagation is None


def test_defaults_when_fields_omitted():
    """Fields should default correctly when not specified in YAML."""
    cfg = RoutingStackConfig(
        name="test",
        image="test:latest",
        config_templates=[],
    )
    assert cfg.segment_routing is False
    assert cfg.ttl_propagation is None
