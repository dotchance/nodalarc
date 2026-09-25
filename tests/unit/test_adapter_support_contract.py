# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Runtime support answers from the registered adapters' own declarations."""

from __future__ import annotations

import ast
import dataclasses
import math
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
    check_link_rates,
    check_router_domains,
    check_routing_members,
    check_sid_indices,
)
from nodalarc.workloads.adapter import AdapterSupport, BfdSupport, RoutingProtocolSupport

from adapters.registry import _ADAPTERS, adapter_named, registered_adapter_support

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
                address_families=frozenset({"ipv4"}),
                domains_per_router=1,
                link_rate_floor_mbps=None,
            ),
            "static": RoutingProtocolSupport(
                address_families=frozenset({"ipv4", "ipv6"}),
                domains_per_router=None,
                link_rate_floor_mbps=None,
            ),
        }
    ),
    "host-only": AdapterSupport(),
}

_IPV4 = frozenset({"ipv4"})
_DUAL = frozenset({"ipv4", "ipv6"})


@pytest.fixture
def stub_declarations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_support, "registered_adapter_support", lambda: _STUB_SUPPORT)


def test_every_registered_adapter_declares_its_support() -> None:
    declarations = registered_adapter_support()

    assert set(declarations) == {name for name, _support, _loader in _ADAPTERS}
    assert all(isinstance(support, AdapterSupport) for support in declarations.values())


def test_each_renderer_carries_its_registered_declaration() -> None:
    for name, support in registered_adapter_support().items():
        renderer = adapter_named(name)

        assert renderer is not None
        assert renderer.name == name
        assert renderer.support is support
        # One renderer per adapter for the process.
        assert adapter_named(name) is renderer


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
        "rendering = [m for m in sys.modules if m in "
        "('adapters.frr.adapter', 'adapters.frr.stack', 'adapters.frr.template_vars')]\n"
        "assert not rendering, rendering\n"
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
        "members": {"sat-a": _IPV4, "sat-b": _IPV4},
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
    [feature] = _check(protocol="ospf", members={f"sat-{index:02d}": _IPV4 for index in range(12)})

    assert "12 nodes (sat-00, sat-01, sat-02, sat-03, sat-04 and 7 more)" in feature.message


def test_member_check_refuses_a_family_the_adapter_does_not_route(stub_declarations) -> None:
    [feature] = _check(members={"gs-a": _DUAL, "sat-a": _IPV4, "gs-b": _DUAL})

    assert feature.category == FeatureCategory.ROUTING_ADDRESS_FAMILY
    assert feature.value == "isis:ipv6"
    assert "ipv6 routing on protocol 'isis'" in feature.message
    # Only the members carrying the family are named.
    assert "node(s) gs-a, gs-b" in feature.message
    assert "sat-a" not in feature.message


def test_member_check_accepts_every_family_the_adapter_routes(stub_declarations) -> None:
    assert _check(protocol="static", members={"gs-a": _DUAL, "sat-a": _IPV4}) == []


def test_routing_support_declares_known_address_families() -> None:
    with pytest.raises(ValueError, match="declare the address families"):
        RoutingProtocolSupport(
            address_families=frozenset(), domains_per_router=1, link_rate_floor_mbps=None
        )
    with pytest.raises(ValueError, match="unknown address families"):
        RoutingProtocolSupport(
            address_families=frozenset({"ipv4", "appletalk"}),
            domains_per_router=1,
            link_rate_floor_mbps=None,
        )


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
                    "isis": RoutingProtocolSupport(
                        frozenset({"mpls", "segment_routing"}),
                        wide,
                        address_families=_IPV4,
                        domains_per_router=1,
                        link_rate_floor_mbps=0.5,
                        sid_index_capacity=8000,
                    ),
                    "ospf": RoutingProtocolSupport(
                        frozenset({"mpls"}),
                        address_families=_IPV4,
                        domains_per_router=1,
                        link_rate_floor_mbps=1.0,
                    ),
                    "static": RoutingProtocolSupport(
                        address_families=_IPV4,
                        domains_per_router=None,
                        link_rate_floor_mbps=None,
                    ),
                }
            ),
            "b": AdapterSupport(
                routing={
                    "isis": RoutingProtocolSupport(
                        frozenset({"traffic_engineering"}),
                        narrow,
                        address_families=_DUAL,
                        domains_per_router=3,
                        link_rate_floor_mbps=2.0,
                    ),
                    "ospf": RoutingProtocolSupport(
                        frozenset({"mpls"}),
                        narrow,
                        address_families=_IPV4,
                        domains_per_router=None,
                        link_rate_floor_mbps=None,
                    ),
                }
            ),
            "host-only": AdapterSupport(),
        },
    )

    # Every capability and family some adapter renders; BFD bounds spanning
    # both ranges; the most domains per router any adapter renders; the lowest
    # link rate floor and the largest prefix-SID capacity.
    assert registered_routing_support("isis") == RoutingProtocolSupport(
        frozenset({"mpls", "segment_routing", "traffic_engineering"}),
        BfdSupport(
            detect_multiplier=(1, 255), rx_interval_ms=(10, 5000), tx_interval_ms=(10, 9000)
        ),
        address_families=_DUAL,
        domains_per_router=3,
        link_rate_floor_mbps=0.5,
        sid_index_capacity=8000,
    )
    # One adapter renders OSPF BFD, so its range is offered; one renders any
    # number of OSPF domains per router, and one renders every link rate.
    assert registered_routing_support("ospf") == RoutingProtocolSupport(
        frozenset({"mpls"}),
        narrow,
        address_families=_IPV4,
        domains_per_router=None,
        link_rate_floor_mbps=None,
    )
    assert registered_routing_support("static") == RoutingProtocolSupport(
        address_families=_IPV4,
        domains_per_router=None,
        link_rate_floor_mbps=None,
    )
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
            "narrow": AdapterSupport(
                routing={
                    "isis": RoutingProtocolSupport(
                        frozenset(),
                        address_families=_IPV4,
                        domains_per_router=1,
                        link_rate_floor_mbps=10.0,
                    )
                }
            ),
        },
    )

    assert registered_routing_support("isis") == FRR_SUPPORT.routing["isis"]
    assert registered_routing_support("ospf") == FRR_SUPPORT.routing["ospf"]


def test_the_capability_vocabulary_is_the_grammar_capabilities() -> None:
    from nodalarc.models.segment_session import ROUTING_CAPABILITIES, RoutingCapabilities

    assert set(ROUTING_CAPABILITIES) == set(RoutingCapabilities.model_fields)


def test_declarations_outside_the_grammar_are_refused() -> None:
    with pytest.raises(ValueError, match="routing protocol 'rip' outside the grammar"):
        AdapterSupport(
            routing={
                "rip": RoutingProtocolSupport(
                    address_families=_IPV4, domains_per_router=1, link_rate_floor_mbps=None
                )
            }
        )
    with pytest.raises(ValueError, match="capabilities outside the grammar"):
        RoutingProtocolSupport(
            frozenset({"warp"}),
            address_families=_IPV4,
            domains_per_router=1,
            link_rate_floor_mbps=None,
        )


def test_routing_support_renders_at_least_one_domain_per_router() -> None:
    with pytest.raises(ValueError, match="at least one domain per router; got 0"):
        RoutingProtocolSupport(
            address_families=_IPV4, domains_per_router=0, link_rate_floor_mbps=None
        )


def test_router_domain_check_refuses_domains_beyond_the_declaration(stub_declarations) -> None:
    routers = {
        f"gs-{index}": (("core", "isis"), ("edge", "isis"), ("lab", "static"), ("lab2", "static"))
        for index in range(7)
    }
    routers["sat-a"] = (("core", "isis"),)

    [feature] = check_router_domains(adapter="stub", routers=routers)

    # One refusal for the routers sharing the excess domains; static domains
    # combine freely and a router in one IS-IS domain is within the limit.
    assert feature.category == FeatureCategory.ROUTER_DOMAINS
    assert feature.value == "isis:core,edge"
    assert feature.message == (
        "7 nodes (gs-0, gs-1, gs-2, gs-3, gs-4 and 2 more) participate in isis domains "
        "['core', 'edge']; workload adapter 'stub' renders 1 isis domain(s) per router"
    )


def test_router_domain_check_accepts_different_protocols(stub_declarations) -> None:
    routers = {"gs-a": (("core", "isis"), ("lab", "static"))}

    assert check_router_domains(adapter="stub", routers=routers) == []


_LIMITED_SUPPORT = {
    "limited": AdapterSupport(
        routing={
            "isis": RoutingProtocolSupport(
                frozenset({"segment_routing"}),
                address_families=_IPV4,
                domains_per_router=1,
                link_rate_floor_mbps=1.5,
                sid_index_capacity=100,
            ),
            "static": RoutingProtocolSupport(
                address_families=_IPV4, domains_per_router=None, link_rate_floor_mbps=None
            ),
        }
    )
}


@pytest.fixture
def limited_declarations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_support, "registered_adapter_support", lambda: _LIMITED_SUPPORT)


def test_link_rate_check_refuses_links_at_or_below_the_floor(limited_declarations) -> None:
    links = {("sat-a", "isl0"): 1.5, ("sat-a", "isl1"): 1.5001, ("sat-b", "isl0"): 0.064}

    [feature] = check_link_rates(domain_id="core", protocol="isis", adapter="limited", links=links)

    assert feature.category == FeatureCategory.ROUTING_LINK_RATE
    assert feature.value == "isis:1.5"
    assert feature.message == (
        "routing domain 'core' runs isis over fixed links at or below 1.5 Mb/s "
        "(sat-a isl0 at 1.5 Mb/s, sat-b isl0 at 0.064 Mb/s); workload adapter 'limited' "
        "has no isis metric for them"
    )


def test_link_rate_check_names_a_bounded_sample_of_many_links(limited_declarations) -> None:
    links = {(f"sat-{index}", "isl0"): 1.0 for index in range(7)}

    [feature] = check_link_rates(domain_id="core", protocol="isis", adapter="limited", links=links)

    assert "(sat-0 isl0 at 1 Mb/s, " in feature.message
    assert "sat-4 isl0 at 1 Mb/s and 2 more)" in feature.message


def test_link_rate_check_accepts_rates_above_the_floor_and_protocols_without_one(
    limited_declarations,
) -> None:
    assert (
        check_link_rates(
            domain_id="core", protocol="isis", adapter="limited", links={("sat-a", "isl0"): 1.6}
        )
        == []
    )
    assert (
        check_link_rates(
            domain_id="lab", protocol="static", adapter="limited", links={("gs-a", "isl0"): 0.001}
        )
        == []
    )


def test_sid_index_check_refuses_indices_beyond_the_capacity(limited_declarations) -> None:
    [feature] = check_sid_indices(
        domain_id="core", protocol="isis", adapter="limited", indices={"a": 100, "b": 101, "c": 250}
    )

    assert feature.category == FeatureCategory.ROUTING_SID_CAPACITY
    assert feature.value == "isis:100"
    assert feature.message == (
        "routing domain 'core' gives node(s) b, c prefix-SID indices up to 250; "
        "workload adapter 'limited' renders indices up to 100"
    )
    assert (
        check_sid_indices(
            domain_id="core", protocol="isis", adapter="limited", indices={"a": 1, "b": 100}
        )
        == []
    )


def test_routing_support_declares_a_sid_capacity_exactly_with_segment_routing() -> None:
    with pytest.raises(ValueError, match="exactly when it renders segment routing"):
        RoutingProtocolSupport(
            frozenset({"segment_routing"}),
            address_families=_IPV4,
            domains_per_router=1,
            link_rate_floor_mbps=None,
        )
    with pytest.raises(ValueError, match="exactly when it renders segment routing"):
        RoutingProtocolSupport(
            frozenset({"mpls"}),
            address_families=_IPV4,
            domains_per_router=1,
            link_rate_floor_mbps=None,
            sid_index_capacity=10,
        )
    with pytest.raises(ValueError, match="at least one prefix-SID index; got 0"):
        RoutingProtocolSupport(
            frozenset({"segment_routing"}),
            address_families=_IPV4,
            domains_per_router=1,
            link_rate_floor_mbps=None,
            sid_index_capacity=0,
        )


@pytest.mark.parametrize("floor", [-1.0, math.inf, math.nan])
def test_routing_support_declares_a_finite_non_negative_link_rate_floor(floor: float) -> None:
    with pytest.raises(ValueError, match="finite, non-negative link rate floor"):
        RoutingProtocolSupport(
            address_families=_IPV4, domains_per_router=1, link_rate_floor_mbps=floor
        )


def _frr_with_isis(monkeypatch: pytest.MonkeyPatch, **changes) -> None:
    from adapters.frr.support import FRR_SUPPORT

    narrowed = AdapterSupport(
        routing={
            **FRR_SUPPORT.routing,
            "isis": dataclasses.replace(FRR_SUPPORT.routing["isis"], **changes),
        }
    )
    monkeypatch.setattr(runtime_support, "registered_adapter_support", lambda: {"frr": narrowed})


def test_a_session_over_links_at_or_below_its_adapter_floor_is_refused_at_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nodalarc.resolve_session import load_session_resolution_from_file
    from nodalarc.runtime_support import UnsupportedFeatureError

    from tests.catalog_session_fixtures import shipped_read_view

    # The polar shell's optical ISLs transmit 2000 Mb/s; a floor above that
    # leaves no link the adapter could give a metric.
    _frr_with_isis(monkeypatch, link_rate_floor_mbps=5000.0)

    with pytest.raises(UnsupportedFeatureError) as refused:
        load_session_resolution_from_file(
            ROOT / "catalog/nodalarc/sessions/earth-leo-polar.yaml", catalog=shipped_read_view()
        )

    [feature] = refused.value.features
    assert feature.category == FeatureCategory.ROUTING_LINK_RATE
    assert feature.value == "isis:5000"
    assert "runs isis over fixed links at or below 5000 Mb/s (" in feature.message
    assert "isl0 at 2000 Mb/s" in feature.message
    assert feature.message.endswith("workload adapter 'frr' has no isis metric for them")


def test_a_segment_routing_domain_beyond_its_adapter_sid_capacity_is_refused_at_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nodalarc.resolve_session import load_session_resolution_from_file
    from nodalarc.runtime_support import UnsupportedFeatureError

    from tests.catalog_session_fixtures import shipped_read_view

    _frr_with_isis(monkeypatch, sid_index_capacity=10)

    with pytest.raises(UnsupportedFeatureError) as refused:
        load_session_resolution_from_file(
            ROOT / "catalog/nodalarc/sessions/earth-leo-heo-geo-luna-reachability.yaml",
            catalog=shipped_read_view(),
        )

    [feature] = refused.value.features
    assert feature.category == FeatureCategory.ROUTING_SID_CAPACITY
    assert feature.value == "isis:10"
    assert "routing domain 'earth_domain' gives " in feature.message
    assert feature.message.endswith("workload adapter 'frr' renders indices up to 10")
