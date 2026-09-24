# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""FRR stack selection: daemons and fragments for one router's domains."""

from __future__ import annotations

import itertools

import pytest
from nodalarc.models.resolved_session import ResolvedRoutingDomain
from nodalarc.models.segment_session import BfdConfig, RoutingTimers

from adapters.frr.stack import resolve_router_stack, validate_sid_indices
from adapters.frr.support import FRR_SUPPORT

# Every capability set on both IGPs.
_IGP_CASES = [
    (protocol, tuple(sorted(combo)))
    for protocol in ("isis", "ospf")
    for size in range(4)
    for combo in itertools.combinations(("mpls", "segment_routing", "traffic_engineering"), size)
]

_IPV4 = frozenset({"ipv4"})
_DUAL = frozenset({"ipv4", "ipv6"})


def _domain(
    protocol: str,
    capabilities: tuple[str, ...] = (),
    *,
    bfd: bool = False,
    domain_id: str = "d1",
) -> ResolvedRoutingDomain:
    return ResolvedRoutingDomain(
        domain_id=domain_id,
        protocol=protocol,
        node_ids=("node-a",),
        capabilities=capabilities,
        timers=RoutingTimers(bfd=BfdConfig(enabled=bfd)),
    )


def _expected_selection(
    protocol: str, capabilities: tuple[str, ...], bfd: bool, families: frozenset[str]
) -> tuple[str, ...]:
    daemons = ["mgmtd", "zebra"]
    if bfd:
        daemons.append("bfdd")
    if protocol != "static":
        daemons.append(f"{protocol}d")
    if protocol == "ospf" and "ipv6" in families:
        daemons.append("ospf6d")
    if "segment_routing" in capabilities:
        daemons.append("pathd")
    elif "mpls" in capabilities:
        daemons.append("ldpd")
    daemons.append("staticd")
    return tuple(daemons)


@pytest.mark.parametrize(("protocol", "capabilities"), _IGP_CASES)
@pytest.mark.parametrize("bfd", [False, True])
@pytest.mark.parametrize("families", [_IPV4, _DUAL])
def test_one_domain_router_runs_exactly_the_domain_daemons(
    protocol, capabilities, bfd, families
) -> None:
    stack = resolve_router_stack((_domain(protocol, capabilities, bfd=bfd),), families)

    daemons = _expected_selection(protocol, capabilities, bfd, families)
    assert stack.daemons == daemons
    # Every selected daemon has the fragment of its name, except that zebra
    # and mgmtd share the global and zebra fragments.
    assert stack.fragments == ("global", "zebra", *daemons[2:])


def test_ospfv3_assembles_after_ospfv2_and_the_bfd_profiles() -> None:
    stack = resolve_router_stack((_domain("ospf", bfd=True),), _DUAL)

    assert stack.fragments == ("global", "zebra", "bfdd", "ospfd", "ospf6d", "staticd")


def test_static_router_runs_zebra_and_staticd() -> None:
    stack = resolve_router_stack((_domain("static"),), _DUAL)

    assert stack.daemons == ("mgmtd", "zebra", "staticd")
    assert stack.fragments == ("global", "zebra", "staticd")


def test_router_in_two_domains_runs_the_union_of_their_daemons() -> None:
    stack = resolve_router_stack(
        (
            _domain("isis", ("segment_routing",), domain_id="core"),
            _domain("ospf", ("mpls",), bfd=True, domain_id="edge"),
            _domain("static", domain_id="lab"),
        ),
        _DUAL,
    )

    assert stack.daemons == (
        "mgmtd",
        "zebra",
        "bfdd",
        "isisd",
        "ospfd",
        "ospf6d",
        "pathd",
        "ldpd",
        "staticd",
    )
    assert stack.fragments == (
        "global",
        "zebra",
        "bfdd",
        "isisd",
        "ospfd",
        "ospf6d",
        "pathd",
        "ldpd",
        "staticd",
    )


def test_ldp_runs_only_for_a_domain_with_bare_mpls() -> None:
    stack = resolve_router_stack(
        (
            _domain("isis", ("mpls", "segment_routing"), domain_id="core"),
            _domain("static", domain_id="lab"),
        ),
        _IPV4,
    )

    assert "ldpd" not in stack.daemons
    assert "pathd" in stack.daemons


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


def test_frr_renders_one_domain_of_each_igp_per_router() -> None:
    assert {
        protocol: support.domains_per_router for protocol, support in FRR_SUPPORT.routing.items()
    } == {"isis": 1, "ospf": 1, "static": None}


@pytest.mark.parametrize("protocol", ["isis", "ospf"])
def test_a_second_domain_of_one_igp_fails_loudly(protocol) -> None:
    domains = (_domain(protocol, domain_id="a"), _domain(protocol, domain_id="b"))
    with pytest.raises(ValueError, match=rf"renders 1 {protocol} domain\(s\).*\['a', 'b'\]"):
        resolve_router_stack(domains, _IPV4)


def test_static_domains_combine_freely() -> None:
    stack = resolve_router_stack(
        (_domain("static", domain_id="a"), _domain("static", domain_id="b")), _IPV4
    )

    assert stack.daemons == ("mgmtd", "zebra", "staticd")


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
        resolve_router_stack((domain,), _IPV4)


def test_a_router_in_no_domain_fails_loudly() -> None:
    with pytest.raises(ValueError, match="at least one routing domain"):
        resolve_router_stack((), _IPV4)


def test_stack_is_frozen() -> None:
    stack = resolve_router_stack((_domain("ospf"),), _IPV4)
    with pytest.raises(AttributeError):
        stack.daemons = ()  # type: ignore[misc]


class TestSidValidation:
    def test_sid_indices_within_srgb_ok(self) -> None:
        domains = (_domain("isis", ("segment_routing",)),)
        validate_sid_indices(domains, {"d1": {"space-sat-p00s00": 1}})

    def test_segment_routing_requires_resolved_sid_indices(self) -> None:
        domains = (_domain("isis", ("segment_routing",)),)
        with pytest.raises(ValueError, match="requires resolved SID"):
            validate_sid_indices(domains, {})

    def test_sid_indices_must_fit_srgb(self) -> None:
        domains = (_domain("isis", ("segment_routing",)),)
        with pytest.raises(ValueError, match="exceeds SRGB"):
            validate_sid_indices(domains, {"d1": {"space-sat-p00s00": 8001}})

    def test_each_segment_routing_domain_needs_its_own_indices(self) -> None:
        domains = (
            _domain("isis", ("segment_routing",), domain_id="core"),
            _domain("ospf", ("segment_routing",), domain_id="edge"),
        )
        with pytest.raises(ValueError, match="'edge' requires resolved SID"):
            validate_sid_indices(domains, {"core": {"node-a": 1}})

    def test_non_sr_domain_ignores_sid_indices(self) -> None:
        validate_sid_indices((_domain("isis"),), {})
