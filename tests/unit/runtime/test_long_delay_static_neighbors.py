# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Static neighbors on long-delay links: what the manifest asks of each link end, and the link
addresses both ends agree on."""

from __future__ import annotations

import ipaddress

import pytest
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.resolve_session import resolve_session
from nodalarc.runtime_naming import duplicate_link_macs, link_interface_mac
from nodalarc.substrate.manifest_contract import NodeSpec
from nodalarc_operator.session_deployer import _fixed_link_interfaces_by_node
from node_agent.pid_discovery import manifest_facts

from tests.catalog_session_fixtures import SHIPPED_ROOT, shipped_read_view

SESSION = SHIPPED_ROOT / "sessions" / "earth-mars-hdtn-relay.yaml"


@pytest.fixture(scope="module")
def resolved():
    return resolve_session(
        load_configuration_yaml(SESSION.read_bytes()), catalog=shipped_read_view()
    )


def test_a_long_delay_link_end_pins_the_peer_loopbacks_and_a_local_one_pins_nothing(
    resolved,
) -> None:
    nodes = {node.node_id: node for node in resolved.nodes}
    trunk = {
        (c.node_a, c.node_b) for c in resolved.link_candidates if c.delay_class == "long_delay"
    }
    assert trunk == {("earth-relay-geo", "mars-relays-mto")}

    by_node = _fixed_link_interfaces_by_node(resolved)
    pinned = {}
    for node_id, entries in by_node.items():
        for entry in entries:
            if entry["static_neighbors"]:
                pinned[(node_id, entry["peer_node"])] = entry["static_neighbors"]
    loopback = {
        node_id: [
            str(ipaddress.ip_interface(address).ip)
            for address in (nodes[node_id].interfaces.lo0.ipv4, nodes[node_id].interfaces.lo0.ipv6)
            if address is not None
        ]
        for node_id in ("earth-relay-geo", "mars-relays-mto")
    }
    assert pinned == {
        ("earth-relay-geo", "mars-relays-mto"): loopback["mars-relays-mto"],
        ("mars-relays-mto", "earth-relay-geo"): loopback["earth-relay-geo"],
    }


def test_each_end_pins_the_address_the_far_host_gives_the_far_interface(resolved) -> None:
    """The Node Agent on one host derives the far end's link address from the manifest; the far
    host gives its interface that same address."""
    entries = _fixed_link_interfaces_by_node(resolved)["mars-relays-mto"]
    node = NodeSpec.model_validate(
        {
            "node_type": "satellite",
            "host": "node01",
            "sysctls": {"net.ipv4.conf.all.rp_filter": "0"},
            "isl_interfaces": entries,
            "gnd_interfaces": [],
            "mpls_enable": False,
            "remove_default_route": True,
        }
    )
    trunk = next(entry for entry in entries if entry["static_neighbors"])

    facts = manifest_facts(node)

    pinned = dict(facts.static_neighbors)[trunk["name"]]
    assert pinned.lladdr == link_interface_mac(trunk["peer_node"], trunk["peer_iface"])
    assert pinned.addresses == tuple(trunk["static_neighbors"])
    assert [name for name, _ in facts.static_neighbors] == [trunk["name"]]


def test_two_link_interfaces_with_one_derived_address_are_found() -> None:
    """The derivation hashes node and name into 40 bits, so two interfaces can share an address;
    the pair below does (found by search)."""
    collide = [("node-681474", "isl0"), ("node-1809922", "isl0")]
    assert link_interface_mac(*collide[0]) == link_interface_mac(*collide[1]) == "02:c0:d8:c5:f7:7b"

    assert duplicate_link_macs([*collide, ("node-1", "isl0")]) == [
        ("02:c0:d8:c5:f7:7b", ["node-1809922/isl0", "node-681474/isl0"])
    ]
    assert duplicate_link_macs([("node-1", "isl0"), ("node-1", "isl1")]) == []
