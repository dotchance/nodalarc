# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Runtime support answers from the registered adapters' own declarations."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import nodalarc.runtime_support as runtime_support
import pytest
from nodalarc.models.segment_session import BfdConfig
from nodalarc.runtime_support import (
    FeatureCategory,
    RuntimeSupport,
    adapter_renders_routing,
    check_routing_members,
)
from nodalarc.workloads.adapter import AdapterSupport, BfdSupport, RoutingProtocolSupport

from adapters.registry import _ADAPTERS, registered_adapter_support

ROOT = Path(__file__).resolve().parents[2]

_STUB_SUPPORT = {
    "stub": AdapterSupport(
        routing={
            "isis": RoutingProtocolSupport(
                capabilities=frozenset({"mpls"}),
                bfd=BfdSupport(
                    detect_multiplier=(2, 10),
                    rx_interval_ms=(50, 1000),
                    tx_interval_ms=(50, 1000),
                ),
            ),
            "static": RoutingProtocolSupport(),
        }
    ),
    "host-only": AdapterSupport(),
}


@pytest.fixture
def stub_declarations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_support, "registered_adapter_support", lambda: _STUB_SUPPORT)


def test_every_registered_adapter_declares_its_support() -> None:
    declarations = registered_adapter_support()

    assert set(declarations) == {adapter.name for adapter in _ADAPTERS}
    assert all(isinstance(support, AdapterSupport) for support in declarations.values())


def test_runtime_support_sets_are_the_union_of_registered_declarations() -> None:
    declarations = registered_adapter_support()
    protocols = {protocol for support in declarations.values() for protocol in support.routing}
    capabilities = {
        f"{protocol}:{capability}"
        for support in declarations.values()
        for protocol, protocol_support in support.routing.items()
        for capability in protocol_support.capabilities
    }
    for profile in (RuntimeSupport.earth_luna(), RuntimeSupport.earth_multi_regime()):
        assert profile.supported_workload_adapters == set(declarations)
        assert profile.supported_routing_protocols == protocols
        assert profile.supported_routing_capabilities == capabilities


def test_runtime_support_follows_whatever_adapters_are_registered(stub_declarations) -> None:
    support = RuntimeSupport.earth_luna()

    assert support.supported_workload_adapters == {"stub", "host-only"}
    assert support.supported_routing_protocols == {"isis", "static"}
    assert support.supported_routing_capabilities == {"isis:mpls"}
    assert adapter_renders_routing("stub")
    assert not adapter_renders_routing("host-only")
    assert not adapter_renders_routing(None)
    assert not adapter_renders_routing("absent")


def test_runtime_support_names_no_adapter_protocol_or_capability() -> None:
    declared = set(registered_adapter_support())
    for support in registered_adapter_support().values():
        for protocol, protocol_support in support.routing.items():
            declared.add(protocol)
            declared.update(protocol_support.capabilities)
    tree = ast.parse((ROOT / "lib" / "nodalarc" / "runtime_support.py").read_text())
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }

    assert literals.isdisjoint(declared), sorted(literals & declared)


def test_declarations_load_without_the_rendering_dependency() -> None:
    probe = (
        "import sys\n"
        "sys.modules['jinja2'] = None\n"
        "from nodalarc.resolve_session import resolve_session\n"
        "from nodalarc.runtime_support import RuntimeSupport\n"
        "assert RuntimeSupport.earth_luna().supported_workload_adapters\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        env={"PYTHONPATH": f"{ROOT / 'lib'}:{ROOT / 'services'}:{ROOT}"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def _check(**overrides):
    arguments = {
        "domain_id": "space",
        "protocol": "isis",
        "capabilities": (),
        "bfd": BfdConfig(),
        "adapter": "stub",
        "node_ids": ("sat-a", "sat-b"),
    }
    arguments.update(overrides)
    return check_routing_members(**arguments)


def test_member_check_accepts_what_the_adapter_renders(stub_declarations) -> None:
    assert _check(capabilities=("mpls",)) == []
    assert _check(bfd=BfdConfig(enabled=True, detect_multiplier=2, rx_interval_ms=50)) == []
    # Timers of a disabled BFD are not rendered, so they are not checked.
    assert _check(bfd=BfdConfig(enabled=False, detect_multiplier=200)) == []


def test_member_check_refuses_an_unrendered_protocol(stub_declarations) -> None:
    [feature] = _check(protocol="ospf", capabilities=("mpls",))

    assert feature.category == FeatureCategory.ROUTING_PROTOCOL
    assert feature.value == "ospf"
    assert "routing domain 'space'" in feature.message
    assert "workload adapter 'stub'" in feature.message
    assert "sat-a, sat-b" in feature.message


def test_member_check_refuses_an_unrendered_capability(stub_declarations) -> None:
    [feature] = _check(capabilities=("mpls", "segment_routing"))

    assert feature.category == FeatureCategory.ROUTING_CAPABILITY
    assert feature.value == "isis:segment_routing"


def test_member_check_refuses_bfd_the_adapter_does_not_render(stub_declarations) -> None:
    [feature] = _check(protocol="static", bfd=BfdConfig(enabled=True))

    assert feature.category == FeatureCategory.ROUTING_TIMER
    assert feature.value == "static:bfd"


def test_member_check_refuses_each_bfd_timer_outside_the_rendered_bounds(
    stub_declarations,
) -> None:
    features = _check(
        bfd=BfdConfig(enabled=True, detect_multiplier=11, rx_interval_ms=49, tx_interval_ms=1001)
    )

    assert [feature.value for feature in features] == [
        "bfd.detect_multiplier=11",
        "bfd.rx_interval_ms=49",
        "bfd.tx_interval_ms=1001",
    ]
    assert all(feature.category == FeatureCategory.ROUTING_TIMER for feature in features)
    assert "outside the rendered range 2..10" in features[0].message


def test_member_check_names_a_bounded_sample_of_many_members(stub_declarations) -> None:
    [feature] = _check(protocol="ospf", node_ids=tuple(f"sat-{index:02d}" for index in range(12)))

    assert "12 nodes (sat-00, sat-01, sat-02, sat-03, sat-04 and 7 more)" in feature.message


def test_registered_support_combines_every_adapter_rendering_the_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nodalarc.runtime_support import registered_routing_support

    wide = BfdSupport(
        detect_multiplier=(1, 255), rx_interval_ms=(10, 5000), tx_interval_ms=(10, 5000)
    )
    narrow = BfdSupport(
        detect_multiplier=(3, 20), rx_interval_ms=(50, 900), tx_interval_ms=(40, 9000)
    )
    monkeypatch.setattr(
        runtime_support,
        "registered_adapter_support",
        lambda: {
            "a": AdapterSupport(
                routing={
                    "isis": RoutingProtocolSupport(frozenset({"mpls", "segment_routing"}), wide),
                    "ospf": RoutingProtocolSupport(frozenset({"mpls"})),
                    "static": RoutingProtocolSupport(),
                }
            ),
            "b": AdapterSupport(
                routing={
                    "isis": RoutingProtocolSupport(frozenset({"traffic_engineering"}), narrow),
                    "ospf": RoutingProtocolSupport(frozenset({"mpls"}), narrow),
                }
            ),
            "host-only": AdapterSupport(),
        },
    )

    # Every capability some adapter renders; BFD bounds spanning both ranges.
    assert registered_routing_support("isis") == RoutingProtocolSupport(
        frozenset({"mpls", "segment_routing", "traffic_engineering"}),
        BfdSupport(
            detect_multiplier=(1, 255), rx_interval_ms=(10, 5000), tx_interval_ms=(10, 9000)
        ),
    )
    # One adapter renders OSPF BFD, so its range is offered.
    assert registered_routing_support("ospf") == RoutingProtocolSupport(frozenset({"mpls"}), narrow)
    assert registered_routing_support("static") == RoutingProtocolSupport()
    assert registered_routing_support("bgp") is None


def test_an_unused_adapter_never_narrows_registered_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nodalarc.runtime_support import registered_routing_support

    from adapters.frr.support import FRR_SUPPORT

    monkeypatch.setattr(
        runtime_support,
        "registered_adapter_support",
        lambda: {
            "frr": FRR_SUPPORT,
            "narrow": AdapterSupport(routing={"isis": RoutingProtocolSupport(frozenset())}),
        },
    )

    assert registered_routing_support("isis") == FRR_SUPPORT.routing["isis"]
    assert registered_routing_support("ospf") == FRR_SUPPORT.routing["ospf"]
