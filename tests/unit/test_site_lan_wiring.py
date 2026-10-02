# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Site LAN wiring contracts: manifest, planner, render, and readiness.

A site's LAN is one L2 segment created at wiring time — per-host bridge,
member terr0 veths as ports, VXLAN head-end replication between hosts. These
tests pin the seams: the manifest declares exactly what the agent wires, the
planner partitions members deterministically, FRR runs terr0 active only where
the segment has a peer, and readiness counts the wired LAN as real adjacency.
"""

from __future__ import annotations

from copy import deepcopy

import pytest
from nodalarc.runtime_naming import LINUX_IFNAME_MAX
from nodalarc.session_validator import validate_session_readiness
from nodalarc.substrate.manifest_contract import WiringManifest
from nodalarc.vxlan import compute_site_vni
from node_agent.site_lan import plan_site_lan
from pydantic import ValidationError

from tests.catalog_session_fixtures import (
    build_catalog_session_fixture,
)
from tests.catalog_session_fixtures import (
    resolve_catalog_session as resolve_session,
)


def _manifest_data() -> dict:
    return {
        "session_id": "run-test-0001",
        "session_run_id": "run-test-0001",
        "owner_uid": "owner-uid-1",
        "wiring_generation": "sha256:" + "a" * 64,
        "required_phases": [
            "host_path_mtu",
            "managed_interface_cleanup",
            "sysctls",
            "isl_interfaces",
            "mpls",
            "ground_infrastructure",
            "terrestrial_interfaces",
            "pod_route_finalization",
            "pod_security",
        ],
        "nodes": {
            "site-a-gw1": {
                "node_type": "ground_station",
                "host": "node02",
                "gs_name": "site-a-gw1",
                "gs_index": 0,
                "sysctls": {"net.ipv4.ip_forward": "1"},
                "isl_interfaces": [],
                "gnd_interfaces": [{"name": "term0"}],
                "mpls_enable": False,
                "remove_default_route": True,
            },
            "site-a-gw2": {
                "node_type": "ground_station",
                "host": "node02",
                "gs_name": "site-a-gw2",
                "gs_index": 1,
                "sysctls": {"net.ipv4.ip_forward": "1"},
                "isl_interfaces": [],
                "gnd_interfaces": [{"name": "term0"}],
                "mpls_enable": False,
                "remove_default_route": True,
            },
        },
        "ground_bridges": {"site-a-gw1": {}, "site-a-gw2": {}},
        "required_substrate_pairs": [],
        "site_lans": {
            "site-a-lan0": {
                "vni": 4242,
                "members": [
                    {
                        "node_id": "site-a-gw1",
                        "interface": "terr0",
                        "addresses": ["172.16.1.1/24"],
                        "gateways": [],
                        "k3s_node": "node01",
                        "host_ip": "10.0.0.1",
                    },
                    {
                        "node_id": "site-a-gw2",
                        "interface": "terr0",
                        "addresses": ["172.16.1.2/24"],
                        "gateways": [],
                        "k3s_node": "node02",
                        "host_ip": "10.0.0.2",
                    },
                ],
            }
        },
        "isl_link_count": 0,
    }


# A pod's cni0 route set: the CNI default (dst_len 0) plus the kernel
# scope-link subnet route from which the bridge gateway (.1) is derived.


class TestManifestContract:
    def test_segment_members_must_be_manifest_nodes(self) -> None:
        data = _manifest_data()
        data["site_lans"]["site-a-lan0"]["members"].append(
            {
                "node_id": "ghost",
                "interface": "terr0",
                "addresses": ["172.16.1.3/24"],
                "gateways": [],
                "k3s_node": "node01",
                "host_ip": "10.0.0.1",
            }
        )
        with pytest.raises(ValidationError, match="unknown member"):
            WiringManifest.model_validate(data)

    def test_segment_members_require_addresses(self) -> None:
        data = _manifest_data()
        data["site_lans"]["site-a-lan0"]["members"][0]["addresses"] = []
        with pytest.raises(ValidationError, match="at least 1"):
            WiringManifest.model_validate(data)

    def test_host_members_carry_gateway_and_routers_never_do(self) -> None:
        data = _manifest_data()
        data["nodes"]["site-a-host"] = {
            "node_type": "host",
            "host": "node01",
            "sysctls": {"net.ipv4.conf.all.rp_filter": "0"},
            "isl_interfaces": [],
            "gnd_interfaces": [],
            "mpls_enable": False,
            "remove_default_route": True,
        }
        data["site_lans"]["site-a-lan0"]["members"].append(
            {
                "node_id": "site-a-host",
                "interface": "terr0",
                "addresses": ["172.16.1.9/24"],
                "gateways": ["172.16.1.1"],
                "k3s_node": "node01",
                "host_ip": "10.0.0.1",
            }
        )
        WiringManifest.model_validate(data)

        dual_stack = deepcopy(data)
        host = dual_stack["site_lans"]["site-a-lan0"]["members"][2]
        host["addresses"] = ["172.16.1.9/24", "fd00:da7a::9/64"]
        host["gateways"] = ["172.16.1.1", "fd00:da7a::1"]
        WiringManifest.model_validate(dual_stack)

        missing_gateway = deepcopy(data)
        missing_gateway["site_lans"]["site-a-lan0"]["members"][2]["gateways"] = []
        with pytest.raises(ValidationError, match="requires a segment gateway"):
            WiringManifest.model_validate(missing_gateway)

        router_with_gateway = deepcopy(data)
        router_with_gateway["site_lans"]["site-a-lan0"]["members"][0]["gateways"] = ["172.16.1.1"]
        with pytest.raises(ValidationError, match="only host nodes carry"):
            WiringManifest.model_validate(router_with_gateway)

    def test_member_gateways_follow_the_member_address_families(self) -> None:
        data = _manifest_data()
        member = data["site_lans"]["site-a-lan0"]["members"][0]

        member["gateways"] = ["172.16.1.254", "172.16.1.253"]
        with pytest.raises(ValidationError, match="two gateways in one family"):
            WiringManifest.model_validate(data)

        member["gateways"] = ["fd00:da7a::1"]
        with pytest.raises(ValidationError, match="holds no address in"):
            WiringManifest.model_validate(data)

    def test_plan_threads_gateway_to_member_port(self) -> None:
        data = _manifest_data()
        spec, nodes = data["site_lans"]["site-a-lan0"], data["nodes"]
        spec["members"][0]["addresses"] = ["172.16.1.1/24", "fd00:da7a::1/64"]
        spec["members"][0]["gateways"] = ["172.16.1.254", "fd00:da7a::fe"]
        plan = plan_site_lan(
            "site-a-lan0",
            spec,
            nodes=nodes,
            pid_map={"site-a-gw1": 111},
            local_node="node01",
            local_ip="10.0.0.1",
            base_mtu=9000,
        )
        assert plan is not None
        assert plan.local_members[0].gateways == ("172.16.1.254", "fd00:da7a::fe")
        assert plan.local_members[0].interface == "terr0"
        assert plan.local_members[0].addresses == ("172.16.1.1/24", "fd00:da7a::1/64")

    def test_segment_vnis_must_be_distinct(self) -> None:
        data = _manifest_data()
        data["site_lans"]["site-b-lan0"] = deepcopy(data["site_lans"]["site-a-lan0"])
        data["site_lans"]["site-b-lan0"]["members"] = [
            deepcopy(data["site_lans"]["site-a-lan0"]["members"][1])
        ]
        data["site_lans"]["site-a-lan0"]["members"] = data["site_lans"]["site-a-lan0"]["members"][
            :1
        ]
        with pytest.raises(ValidationError, match="pairwise distinct"):
            WiringManifest.model_validate(data)


class TestPlanner:
    def _spec_and_nodes(self) -> tuple[dict, dict]:
        data = _manifest_data()
        return data["site_lans"]["site-a-lan0"], data["nodes"]

    def test_partitions_local_members_and_peer_hosts(self) -> None:
        spec, nodes = self._spec_and_nodes()
        plan = plan_site_lan(
            "site-a",
            spec,
            nodes=nodes,
            pid_map={"site-a-gw1": 111},
            local_node="node01",
            local_ip="10.0.0.1",
            base_mtu=9000,
        )
        assert plan is not None
        assert [port.node_id for port in plan.local_members] == ["site-a-gw1"]
        assert plan.local_members[0].addresses == ("172.16.1.1/24",)
        assert plan.peer_host_ips == ("10.0.0.2",)
        assert plan.vxlan_ifname is not None
        # The hosts carry the VXLAN overhead, so a cross-host LAN keeps the full MTU.
        assert plan.mtu == 9000
        for name in (
            plan.bridge,
            plan.vxlan_ifname,
            plan.local_members[0].host_ifname,
            plan.local_members[0].pod_ifname,
        ):
            assert len(name) <= LINUX_IFNAME_MAX

    def test_single_host_site_has_no_vxlan_port(self) -> None:
        spec, nodes = self._spec_and_nodes()
        for member in spec["members"]:
            member["k3s_node"] = "node01"
        plan = plan_site_lan(
            "site-a",
            spec,
            nodes=nodes,
            pid_map={"site-a-gw1": 111, "site-a-gw2": 222},
            local_node="node01",
            local_ip="10.0.0.1",
            base_mtu=9000,
        )
        assert plan is not None
        assert len(plan.local_members) == 2
        assert plan.vxlan_ifname is None
        assert plan.peer_host_ips == ()
        # Placement never changes the LAN's MTU: single-host equals cross-host.
        assert plan.mtu == 9000
        # Member interface names are index-deterministic across hosts.
        assert plan.local_members[0].host_ifname != plan.local_members[1].host_ifname

    def test_no_local_members_means_no_plan(self) -> None:
        spec, nodes = self._spec_and_nodes()
        plan = plan_site_lan(
            "site-a",
            spec,
            nodes=nodes,
            pid_map={},
            local_node="node09",
            local_ip="10.0.0.9",
            base_mtu=9000,
        )
        assert plan is None

    def test_placed_member_without_local_pod_fails_loudly(self) -> None:
        spec, nodes = self._spec_and_nodes()
        with pytest.raises(RuntimeError, match="no local pod"):
            plan_site_lan(
                "site-a",
                spec,
                nodes=nodes,
                pid_map={},
                local_node="node01",
                local_ip="10.0.0.1",
                base_mtu=9000,
            )

    def test_cross_host_site_requires_host_ip(self) -> None:
        spec, nodes = self._spec_and_nodes()
        with pytest.raises(RuntimeError, match="HOST_IP"):
            plan_site_lan(
                "site-a",
                spec,
                nodes=nodes,
                pid_map={"site-a-gw1": 111},
                local_node="node01",
                local_ip="",
                base_mtu=9000,
            )

    def test_declared_uplink_is_never_silently_ignored(self) -> None:
        spec, nodes = self._spec_and_nodes()
        spec["uplink"] = {"host": "node03", "interface": "eno2"}
        with pytest.raises(RuntimeError, match="uplink"):
            plan_site_lan(
                "site-a",
                spec,
                nodes=nodes,
                pid_map={"site-a-gw1": 111},
                local_node="node01",
                local_ip="10.0.0.1",
                base_mtu=9000,
            )


def _two_node_site_session() -> dict:
    raw = build_catalog_session_fixture(
        name="site-lan-render",
        constellation={"planes": {"count": 1, "sats_per_plane": 2}},
        ground_stations={"stations": ["a"]},
    )
    site_document = raw.read_catalog(raw.site_refs[0])
    site = site_document["site"]
    second = deepcopy(site["nodes"][0])
    second["id"] = "gw2"
    second["interfaces"] = {"terr0": "lan0"}
    site["nodes"].append(second)
    raw.write_catalog(raw.site_refs[0], site_document)
    return raw


class TestRenderAndReadiness:
    def test_multi_node_site_runs_terr0_active_single_node_stays_passive(self) -> None:
        from nodalarc.models.resolved_session import SourceContext
        from nodalarc.workloads.adapter import SessionContext

        from adapters.frr.template_vars import build_template_vars_from_resolved

        def terr0_facts(resolved, node):
            vars_for_node = build_template_vars_from_resolved(
                SessionContext(resolved=resolved),
                node,
                domains=resolved.routing_domains_for(node.node_id),
                sid_by_domain=resolved.sid_index_by_domain(),
            )
            [domain] = vars_for_node["domains"]
            return next(seg for seg in domain["segments"] if seg["name"] == "terr0")

        resolved = resolve_session(
            _two_node_site_session(),
            source_context=SourceContext(origin="test.site_lan", run_id="run-test-0001"),
        )
        ground = [n for n in resolved.nodes if n.kind == "ground_station"]
        assert len(ground) == 2
        for node in ground:
            assert terr0_facts(resolved, node)["igp_active"] is True

        single = resolve_session(
            build_catalog_session_fixture(
                name="site-lan-single",
                constellation={"planes": {"count": 1, "sats_per_plane": 2}},
                ground_stations={"stations": ["a"]},
            ),
            source_context=SourceContext(origin="test.site_lan", run_id="run-test-0002"),
        )
        lone = next(n for n in single.nodes if n.kind == "ground_station")
        assert terr0_facts(single, lone)["igp_active"] is False

    def test_site_lan_membership_satisfies_domain_connectivity(self) -> None:
        from nodalarc.models.resolved_session import SourceContext

        # A routed site node with NO terminals (so zero link candidates of
        # its own) must still pass readiness: the wired site LAN is real
        # adjacency and connectivity validation counts it. Persisted test
        # catalog, not a shipped session — shipped content evolves, the invariant
        # does not.
        raw = build_catalog_session_fixture(
            name="site-lan-conn",
            constellation={"planes": {"count": 1, "sats_per_plane": 2}},
            ground_stations={"stations": ["a"]},
        )
        site_document = raw.read_catalog(raw.site_refs[0])
        site = site_document["site"]
        second = deepcopy(site["nodes"][0])
        second["id"] = "gw2"
        second["terminals"] = {}
        second["interfaces"] = {"terr0": "lan0"}
        site["nodes"].append(second)
        raw.write_catalog(raw.site_refs[0], site_document)
        resolved = resolve_session(
            raw,
            source_context=SourceContext(origin="test.site_lan", run_id="run-test-0003"),
        )
        gw2 = next(n for n in resolved.nodes if n.node_id.endswith("gw2"))
        gw2_candidates = [
            c for c in resolved.link_candidates if gw2.node_id in (c.node_a, c.node_b)
        ]
        assert gw2_candidates == []
        errors = [
            result
            for result in validate_session_readiness(resolved, available_node_count=4)
            if result.level == "error"
        ]
        assert errors == []


def test_site_vni_is_deterministic_and_in_range() -> None:
    a = compute_site_vni("earth-us-co-denver")
    assert a == compute_site_vni("earth-us-co-denver")
    assert 1 <= a <= 16777214
    assert a != compute_site_vni("earth-dj-djibouti")
