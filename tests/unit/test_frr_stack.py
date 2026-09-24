# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""FRR stack selection: daemons, fragments and inputs per resolved domain."""

from __future__ import annotations

import itertools

import pytest
from nodalarc.models.resolved_session import ResolvedRoutingDomain
from nodalarc.models.segment_session import BfdConfig, RoutingTimers

from adapters.frr.stack import resolve_domain_stack, validate_sid_indices
from adapters.frr.support import FRR_SUPPORT

# Every capability set on both IGPs.
_IGP_CASES = [
    (protocol, tuple(sorted(combo)))
    for protocol in ("isis", "ospf")
    for size in range(4)
    for combo in itertools.combinations(("mpls", "segment_routing", "traffic_engineering"), size)
]


def _domain(
    protocol: str,
    capabilities: tuple[str, ...] = (),
    *,
    bfd: bool = False,
) -> ResolvedRoutingDomain:
    return ResolvedRoutingDomain(
        domain_id="d1",
        protocol=protocol,
        node_ids=("node-a",),
        capabilities=capabilities,
        timers=RoutingTimers(bfd=BfdConfig(enabled=bfd)),
    )


def _expected_selection(
    protocol: str, capabilities: tuple[str, ...], bfd: bool
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    daemons = ["mgmtd", "zebra"]
    fragments = ["global", "zebra"]
    if bfd:
        daemons.append("bfdd")
        fragments.append("bfdd")
    if protocol != "static":
        daemons.append(f"{protocol}d")
        fragments.append(f"{protocol}d")
    if protocol == "ospf":
        daemons.append("ospf6d")
        fragments.append("ospf6d")
    if "segment_routing" in capabilities:
        daemons.append("pathd")
        fragments.append("pathd")
    elif "mpls" in capabilities:
        daemons.append("ldpd")
        fragments.append("ldpd")
    daemons.append("staticd")
    fragments.append("staticd")
    return tuple(daemons), tuple(fragments)


@pytest.mark.parametrize(("protocol", "capabilities"), _IGP_CASES)
@pytest.mark.parametrize("bfd", [False, True])
def test_igp_stack_selects_exactly_the_domain_daemons(protocol, capabilities, bfd) -> None:
    stack = resolve_domain_stack(_domain(protocol, capabilities, bfd=bfd))

    daemons, fragments = _expected_selection(protocol, capabilities, bfd)
    assert stack.daemons == daemons
    assert stack.fragments == fragments
    assert ("bfdd" in stack.daemons) is bfd
    # SR-MPLS carries the MPLS data plane; LDP runs only for bare mpls.
    assert ("ldpd" in stack.daemons) is (
        "mpls" in capabilities and "segment_routing" not in capabilities
    )
    assert stack.segment_routing is ("segment_routing" in capabilities)
    assert stack.template_variables["log_file"] == f"/var/log/frr/{protocol}d.log"
    assert stack.template_variables["te_enabled"] is ("traffic_engineering" in capabilities)
    assert stack.template_variables["sr_enabled"] is ("segment_routing" in capabilities)
    assert stack.mi_adapter == f"frr_{protocol}_adapter"
    assert stack.image == "frr"


_IPV4 = frozenset({"ipv4"})
_DUAL = frozenset({"ipv4", "ipv6"})


@pytest.mark.parametrize("bfd", [False, True])
def test_only_ipv6_ospf_members_run_ospfv3(bfd) -> None:
    stack = resolve_domain_stack(_domain("ospf", ("mpls",), bfd=bfd))

    assert stack.member_daemons(_DUAL) == stack.daemons
    assert stack.member_fragments(_DUAL) == stack.fragments
    assert stack.member_daemons(_IPV4) == tuple(d for d in stack.daemons if d != "ospf6d")
    assert stack.member_fragments(_IPV4) == tuple(f for f in stack.fragments if f != "ospf6d")
    # OSPFv3 assembles after OSPFv2 and after the BFD profile it references.
    fragments = stack.member_fragments(_DUAL)
    assert fragments.index("ospf6d") == fragments.index("ospfd") + 1


@pytest.mark.parametrize("protocol", ["isis", "static"])
def test_ipv6_needs_no_extra_daemon_outside_ospf(protocol) -> None:
    stack = resolve_domain_stack(_domain(protocol))

    assert stack.member_daemons(_DUAL) == stack.member_daemons(_IPV4) == stack.daemons
    assert stack.member_fragments(_DUAL) == stack.member_fragments(_IPV4) == stack.fragments


def test_frr_declares_the_capabilities_its_fragments_render() -> None:
    igp = {"mpls", "segment_routing", "traffic_engineering"}
    assert FRR_SUPPORT.routing["isis"].capabilities == igp
    assert FRR_SUPPORT.routing["ospf"].capabilities == igp
    assert FRR_SUPPORT.routing["static"].capabilities == frozenset()
    assert FRR_SUPPORT.routing["static"].bfd is None


def test_frr_routes_both_address_families_on_every_protocol() -> None:
    assert {
        protocol: support.address_families for protocol, support in FRR_SUPPORT.routing.items()
    } == {"isis": _DUAL, "ospf": _DUAL, "static": _DUAL}


def test_static_stack_runs_zebra_and_staticd_without_an_igp_log() -> None:
    stack = resolve_domain_stack(_domain("static"))

    assert stack.daemons == ("mgmtd", "zebra", "staticd")
    assert stack.fragments == ("global", "zebra", "staticd")
    assert stack.template_variables["log_file"] is None
    assert stack.mi_adapter is None
    assert stack.segment_routing is False


@pytest.mark.parametrize("protocol", ["isis", "ospf"])
def test_segment_routing_stack_carries_the_srgb_and_srlb(protocol) -> None:
    stack = resolve_domain_stack(_domain(protocol, ("segment_routing",)))

    assert stack.template_variables["srgb_start"] == 16000
    assert stack.template_variables["srgb_end"] == 23999
    assert stack.template_variables["srlb_start"] == 40000
    assert stack.template_variables["srlb_end"] == 49999


@pytest.mark.parametrize(
    ("domain", "message"),
    [
        (_domain("bgp"), "does not render"),
        (_domain("static", ("mpls",)), "does not render for 'static'"),
        (_domain("static", bfd=True), "enables BFD"),
    ],
)
def test_domains_outside_the_frr_declaration_fail_loudly(domain, message) -> None:
    with pytest.raises(ValueError, match=message):
        resolve_domain_stack(domain)


def test_stack_is_frozen() -> None:
    stack = resolve_domain_stack(_domain("ospf"))
    with pytest.raises(AttributeError):
        stack.image = "something"  # type: ignore[misc]


class TestSidValidation:
    def test_sid_indices_within_srgb_ok(self) -> None:
        stack = resolve_domain_stack(_domain("isis", ("segment_routing",)))
        validate_sid_indices(stack, {"space-sat-p00s00": 1})

    def test_segment_routing_requires_resolved_sid_indices(self) -> None:
        stack = resolve_domain_stack(_domain("isis", ("segment_routing",)))
        with pytest.raises(ValueError, match="requires resolved SID"):
            validate_sid_indices(stack, {})

    def test_sid_indices_must_fit_srgb(self) -> None:
        stack = resolve_domain_stack(_domain("isis", ("segment_routing",)))
        with pytest.raises(ValueError, match="exceeds SRGB"):
            validate_sid_indices(stack, {"space-sat-p00s00": 8001})

    def test_non_sr_stack_ignores_sid_indices(self) -> None:
        validate_sid_indices(resolve_domain_stack(_domain("isis")), {})


def test_ldp_domain_with_bfd_selects_the_literal_daemon_list() -> None:
    stack = resolve_domain_stack(_domain("isis", ("mpls",), bfd=True))

    assert stack.daemons == ("mgmtd", "zebra", "bfdd", "isisd", "ldpd", "staticd")
    assert stack.fragments == ("global", "zebra", "bfdd", "isisd", "ldpd", "staticd")
