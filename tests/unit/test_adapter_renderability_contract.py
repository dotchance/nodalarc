# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The renderability declaration stays aligned with the adapter packages."""

from __future__ import annotations

from nodalarc.runtime_support import (
    ADAPTER_RENDERED_CAPABILITIES,
    RuntimeSupport,
    adapter_renders,
    adapter_renders_routing,
)

from adapters.registry import _ADAPTERS


def test_declared_adapters_match_the_registered_adapter_packages() -> None:
    registered = {type(adapter).__module__.split(".")[1] for adapter in _ADAPTERS}

    assert set(ADAPTER_RENDERED_CAPABILITIES) == registered


def test_supported_adapters_are_declared_and_frr_matches_the_stack() -> None:
    for profile in (RuntimeSupport.earth_luna(), RuntimeSupport.earth_multi_regime()):
        assert profile.supported_workload_adapters <= set(ADAPTER_RENDERED_CAPABILITIES)

    frr = ADAPTER_RENDERED_CAPABILITIES["frr"]
    support = RuntimeSupport.earth_luna()
    assert set(frr) == set(support.supported_routing_protocols)
    for protocol in ("isis", "ospf"):
        assert {
            f"{protocol}:{capability}" for capability in frr[protocol]
        } <= support.supported_routing_capabilities
    assert frr["static"] == frozenset()


def test_renderability_predicates_answer_from_the_declaration() -> None:
    assert adapter_renders("frr", "isis")
    assert adapter_renders("frr", "isis", ("mpls", "segment_routing"))
    assert not adapter_renders("frr", "bgp")
    assert not adapter_renders("frr", "static", ("mpls",))
    assert not adapter_renders(None, "isis")
    assert not adapter_renders("absent", "isis")
    assert adapter_renders_routing("frr")
    assert not adapter_renders_routing(None)
    assert not adapter_renders_routing("absent")
