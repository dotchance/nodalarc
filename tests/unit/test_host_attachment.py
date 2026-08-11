# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Host-forwarding nodes: derived substrate attachment, no invented routing."""

from __future__ import annotations

from pathlib import Path

import pytest
from nodalarc.resolve_session import SessionResolutionError
from nodalarc.workloads.refs import ImplementationBindingRef, selection_ref_from_spec
from nodalarc.workloads.source import DirectoryPackageSource
from nodalarc_operator.session_deployer import prepare_session_workloads
from nodalarc_operator.workloads.selection import (
    WorkloadSelectionError,
    prepare_workload_selection,
)

from tests.catalog_session_fixtures import (
    build_catalog_session_fixture,
    resolve_catalog_session,
)


def _resolved_with_hosts(**overrides):
    fixture = build_catalog_session_fixture(
        name=overrides.pop("name", "host-attachment"),
        constellation={},
        ground_stations={"stations": [{}, {}], "host_endpoints": True},
        **overrides,
    )
    return resolve_catalog_session(fixture)


def test_host_nodes_resolve_with_derived_attachment() -> None:
    resolved = _resolved_with_hosts()
    hosts = [node for node in resolved.nodes if node.forwarding == "host"]
    routers = {node.node_id: node for node in resolved.nodes if node.forwarding == "routed"}
    assert len(hosts) == 2
    for host in hosts:
        attachment = host.host_attachment
        assert attachment is not None
        assert attachment.interface == "terr0"
        # The host's address is its authored terr0 placement.
        assert attachment.ipv4 == host.interfaces.terr0.ipv4
        # The gateway is the routed node sharing the site LAN, named by id,
        # and its address is that node's terr0 without the prefix.
        gateway = routers[attachment.gateway_node_id]
        assert gateway.interfaces.terr0.ipv4.split("/", 1)[0] == attachment.gateway_ipv4
        assert attachment.ipv4.split("/", 1)[1] == gateway.interfaces.terr0.ipv4.split("/", 1)[1]


def test_routed_nodes_carry_no_attachment() -> None:
    resolved = _resolved_with_hosts(name="host-attachment-none")
    for node in resolved.nodes:
        if node.forwarding != "host":
            assert node.host_attachment is None


def test_hosts_stay_out_of_the_default_routing_domain() -> None:
    resolved = _resolved_with_hosts(name="host-attachment-domain")
    host_ids = {node.node_id for node in resolved.nodes if node.forwarding == "host"}
    assert host_ids
    for domain in resolved.routing_domains:
        assert not host_ids.intersection(domain.node_ids)


def test_explicit_domains_are_defined_over_routed_nodes() -> None:
    """A set selector sweeping hosts takes its routed subset — the same
    universe as the documented no-routing default — and hosts never appear
    in any domain."""
    resolved = _resolved_with_hosts(
        name="host-attachment-explicit",
        routing={
            "domains": [
                {
                    "id": "everything",
                    "protocol": "isis",
                    "selectors": [{"any": [{"segment": "space"}, {"segment": "ground"}]}],
                    "area_assignment": {"strategy": "flat"},
                }
            ]
        },
    )
    host_ids = {node.node_id for node in resolved.nodes if node.forwarding == "host"}
    assert host_ids
    for domain in resolved.routing_domains:
        assert not host_ids.intersection(domain.node_ids)


def test_domain_matching_only_hosts_is_refused() -> None:
    with pytest.raises(SessionResolutionError, match="contains zero routers"):
        _resolved_with_hosts(
            name="host-attachment-hosts-only",
            routing={
                "domains": [
                    {
                        "id": "endpoints",
                        "protocol": "isis",
                        "selectors": [{"tag": "test_host"}],
                    },
                    {
                        "id": "rest",
                        "protocol": "isis",
                        "selectors": [{"any": [{"segment": "space"}, {"segment": "ground"}]}],
                        "area_assignment": {"strategy": "flat"},
                    },
                ]
            },
        )


FIXTURE_PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "workloads"
HETERO_BINDING = "nodalarc:bindings/hetero-host.yaml"


def _hetero_selection_ref():
    digest = (
        DirectoryPackageSource(FIXTURE_PACKAGE_ROOT)
        .load(ImplementationBindingRef(HETERO_BINDING))
        .package_digest
    )
    return selection_ref_from_spec(
        {
            "implementationBindingRef": HETERO_BINDING,
            "implementationPackageDigest": digest,
        }
    )


def test_heterogeneous_binding_composes_hosts_without_frr() -> None:
    """Host nodes compose from the plain application profile — one
    zero-capability container, no plan artifacts — while routed nodes keep
    the FRR composition, in ONE selection under one sky."""
    resolved = _resolved_with_hosts(name="host-hetero")
    selected = prepare_workload_selection(
        _hetero_selection_ref(),
        resolved,
        namespace="nodalarc",
        owner_ref={"kind": "ConstellationSpec", "name": "s", "uid": "u1"},
        package_root=FIXTURE_PACKAGE_ROOT,
    )
    assert selected is not None
    hosts = {n.node_id for n in resolved.nodes if n.forwarding == "host"}
    assert hosts
    for node_id, composed in selected.composed.items():
        names = [c.name for c in composed.composition.containers]
        if node_id in hosts:
            assert names == ["app"], names
            assert composed.artifact_config_map is None or not [
                k for k in (composed.artifact_config_map.binary_data or {}) if k.startswith("p-")
            ]
        else:
            assert "frr" in names


def test_builtin_default_refuses_host_nodes() -> None:
    """No built-in host workload exists: the FRR default path refuses a
    session with hosts before any write."""
    from types import SimpleNamespace

    resolved = _resolved_with_hosts(name="host-legacy-refusal")
    active_session = SimpleNamespace(
        workload_selection=None,
        resolution=SimpleNamespace(resolved=resolved),
    )
    with pytest.raises(WorkloadSelectionError, match="explicit workload selection"):
        prepare_session_workloads(
            active_session,
            namespace="nodalarc",
            owner_ref={"kind": "ConstellationSpec", "name": "s", "uid": "u1"},
        )
