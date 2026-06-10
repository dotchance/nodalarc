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
from nodalarc.resolve_session import resolve_session
from nodalarc.runtime_naming import LINUX_IFNAME_MAX, is_managed_host_ifname
from nodalarc.session_validator import validate_session_readiness
from nodalarc.substrate.manifest_contract import WiringManifest
from nodalarc.vxlan import compute_site_vni
from node_agent.site_lan import plan_site_lan
from pydantic import ValidationError

from tests.conftest import build_segment_session_dict


def _manifest_data() -> dict:
    return {
        "session_id": "run-test-0001",
        "wiring_generation": "sha256:" + "a" * 64,
        "required_phases": [
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
                "gs_name": "site-a-gw1",
                "gs_index": 0,
                "sysctls": {"net.ipv4.ip_forward": "1"},
                "isl_interfaces": [],
                "gnd_interfaces": [{"name": "term0"}],
                "terrestrial": {"addresses": ["172.16.1.1/24"], "site_id": "site-a"},
                "mpls_enable": False,
                "segment_routing": False,
                "mtu": 9000,
                "remove_default_route": True,
            },
            "site-a-gw2": {
                "node_type": "ground_station",
                "gs_name": "site-a-gw2",
                "gs_index": 1,
                "sysctls": {"net.ipv4.ip_forward": "1"},
                "isl_interfaces": [],
                "gnd_interfaces": [{"name": "term0"}],
                "terrestrial": {"addresses": ["172.16.1.2/24"], "site_id": "site-a"},
                "mpls_enable": False,
                "segment_routing": False,
                "mtu": 9000,
                "remove_default_route": True,
            },
        },
        "ground_bridges": {"site-a-gw1": {}, "site-a-gw2": {}},
        "required_substrate_pairs": [],
        "site_lans": {
            "site-a": {
                "vni": 4242,
                "members": [
                    {"node_id": "site-a-gw1", "k3s_node": "node01", "host_ip": "10.0.0.1"},
                    {"node_id": "site-a-gw2", "k3s_node": "node02", "host_ip": "10.0.0.2"},
                ],
            }
        },
        "isl_link_count": 0,
    }


class TestManifestContract:
    def test_site_lan_manifest_round_trips(self) -> None:
        manifest = WiringManifest.model_validate(_manifest_data())
        spec = manifest.site_lans["site-a"]
        assert spec.vni == 4242
        assert [m.node_id for m in spec.members] == ["site-a-gw1", "site-a-gw2"]
        assert spec.uplink is None

    def test_addressed_terr0_requires_site_membership(self) -> None:
        data = _manifest_data()
        data["site_lans"]["site-a"]["members"] = data["site_lans"]["site-a"]["members"][:1]
        with pytest.raises(ValidationError, match="not a declared member"):
            WiringManifest.model_validate(data)

    def test_addressed_terr0_requires_site_id(self) -> None:
        data = _manifest_data()
        del data["nodes"]["site-a-gw1"]["terrestrial"]["site_id"]
        with pytest.raises(ValidationError, match="require site_id"):
            WiringManifest.model_validate(data)

    def test_site_lan_members_must_be_ground_nodes(self) -> None:
        data = _manifest_data()
        data["site_lans"]["site-a"]["members"].append(
            {"node_id": "ghost", "k3s_node": "node01", "host_ip": "10.0.0.1"}
        )
        with pytest.raises(ValidationError, match="non-ground member"):
            WiringManifest.model_validate(data)

    def test_site_lan_vnis_must_be_distinct(self) -> None:
        data = _manifest_data()
        data["site_lans"]["site-b"] = deepcopy(data["site_lans"]["site-a"])
        data["site_lans"]["site-b"]["members"] = [
            {"node_id": "site-a-gw2", "k3s_node": "node02", "host_ip": "10.0.0.2"}
        ]
        data["nodes"]["site-a-gw2"]["terrestrial"]["site_id"] = "site-b"
        data["site_lans"]["site-a"]["members"] = data["site_lans"]["site-a"]["members"][:1]
        with pytest.raises(ValidationError, match="pairwise distinct"):
            WiringManifest.model_validate(data)

    def test_uplink_slot_round_trips(self) -> None:
        data = _manifest_data()
        data["site_lans"]["site-a"]["uplink"] = {"host": "node03", "interface": "eno2"}
        manifest = WiringManifest.model_validate(data)
        assert manifest.site_lans["site-a"].uplink.interface == "eno2"


class TestPlanner:
    def _spec_and_nodes(self) -> tuple[dict, dict]:
        data = _manifest_data()
        return data["site_lans"]["site-a"], data["nodes"]

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
        assert plan.mtu == 9000 - 50
        for name in (
            plan.bridge,
            plan.vxlan_ifname,
            plan.local_members[0].host_ifname,
            plan.local_members[0].pod_ifname,
        ):
            assert len(name) <= LINUX_IFNAME_MAX
            assert is_managed_host_ifname(name)

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
    raw = build_segment_session_dict(
        name="site-lan-render",
        constellation={"planes": {"count": 1, "sats_per_plane": 2}},
        ground_stations={"stations": ["a"]},
    )
    site = raw["segments"][1]["placement"]["from_site_set"]["site_set"]["sites"][0]["site"]
    second = deepcopy(site["nodes"][0])
    second["id"] = "gw2"
    second["interfaces"] = {
        "lo0": {"ipv4": "10.255.0.201/32", "ipv6": "fd00:da7a:ffff::c9/128"},
        "terr0": {"ipv4": "172.16.0.2/24", "ipv6": "fd10:0:0::2/64"},
    }
    site["nodes"].append(second)
    return raw


class TestRenderAndReadiness:
    def test_multi_node_site_runs_terr0_active_single_node_stays_passive(self) -> None:
        from nodalarc.models.resolved_session import SourceContext
        from nodalarc.stack_resolver import resolve_domain_stack
        from nodalarc.template_vars import build_template_vars_from_resolved

        resolved = resolve_session(
            _two_node_site_session(),
            source_context=SourceContext(origin="test.site_lan", run_id="run-test-0001"),
        )
        ground = [n for n in resolved.nodes if n.kind == "ground_station"]
        assert len(ground) == 2

        domain = resolved.routing_domains[0]
        stack = resolve_domain_stack(domain)
        for node in ground:
            vars_for_node = build_template_vars_from_resolved(
                resolved, node.node_id, stack_variables=stack.template_variables
            )
            assert vars_for_node["terr0_igp_active"] is True

        single = resolve_session(
            build_segment_session_dict(
                name="site-lan-single",
                constellation={"planes": {"count": 1, "sats_per_plane": 2}},
                ground_stations={"stations": ["a"]},
            ),
            source_context=SourceContext(origin="test.site_lan", run_id="run-test-0002"),
        )
        lone = next(n for n in single.nodes if n.kind == "ground_station")
        lone_vars = build_template_vars_from_resolved(
            single, lone.node_id, stack_variables=stack.template_variables
        )
        assert lone_vars["terr0_igp_active"] is False

    def test_site_lan_membership_satisfies_domain_connectivity(self) -> None:
        from nodalarc.models.resolved_session import SourceContext

        # A routed site node with NO terminals (so zero link candidates of
        # its own) must still pass readiness: the wired site LAN is real
        # adjacency and connectivity validation counts it. Inline fixture,
        # not a shipped session — shipped content evolves, the invariant
        # does not.
        raw = build_segment_session_dict(
            name="site-lan-conn",
            constellation={"planes": {"count": 1, "sats_per_plane": 2}},
            ground_stations={"stations": ["a"]},
        )
        site = raw["segments"][1]["placement"]["from_site_set"]["site_set"]["sites"][0]["site"]
        second = deepcopy(site["nodes"][0])
        second["id"] = "gw2"
        second["terminals"] = {}
        second["interfaces"] = {
            "lo0": {"ipv4": "10.255.0.201/32", "ipv6": "fd00:da7a:ffff::c9/128"},
            "terr0": {"ipv4": "172.16.0.2/24", "ipv6": "fd10:0:0::2/64"},
        }
        site["nodes"].append(second)
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


class _FakeIPRoute:
    """Scripted netlink stand-in for actuation-contract tests.

    Tracks links by name and can be told that an address-add loses the race
    to another writer (FRR zebra applies terr0 addresses from frr.conf the
    instant the interface exists).
    """

    def __init__(self, existing: dict[str, int] | None = None, addr_exists: bool = False):
        self.links: dict[str, int] = dict(existing or {})
        self.addr_exists = addr_exists
        self.ops: list[tuple] = []
        self._next_index = 1000

    def link_lookup(self, ifname: str):
        return [self.links[ifname]] if ifname in self.links else []

    def link(self, action: str, **kwargs):
        self.ops.append(("link", action, kwargs))
        if action == "add":
            self.links[kwargs["ifname"]] = self._next_index
            if "peer" in kwargs:
                self.links[kwargs["peer"]["ifname"]] = self._next_index + 1
                self._next_index += 1
            self._next_index += 1
        elif action == "del":
            index = kwargs["index"]
            self.links = {name: idx for name, idx in self.links.items() if idx != index}
        elif action == "set" and "ifname" in kwargs:
            index = kwargs["index"]
            for name, idx in list(self.links.items()):
                if idx == index:
                    del self.links[name]
                    self.links[kwargs["ifname"]] = idx

    def addr(self, action: str, **kwargs):
        self.ops.append(("addr", action, kwargs))
        if action == "add" and self.addr_exists:
            raise Exception(17, "File exists")

    def fdb(self, action: str, **kwargs):
        self.ops.append(("fdb", action, kwargs))


class TestActuationIdempotency:
    """The EEXIST class: pre-existing kernel state is desired state or stale
    state — never an error. Zebra races and wiring re-runs both land here."""

    def test_addr_add_losing_the_zebra_race_is_success(self, monkeypatch) -> None:
        from node_agent import site_lan

        fake = _FakeIPRoute(existing={"sp0000424200": 7}, addr_exists=True)
        monkeypatch.setattr(site_lan, "_in_namespace", lambda pid, fn: fn(fake))
        port = site_lan.MemberPort(
            node_id="site-a-gw1",
            pid=111,
            host_ifname="sm0000424200",
            pod_ifname="sp0000424200",
            addresses=("172.16.1.1/24", "fd10::1/64"),
        )
        site_lan._configure_member_pod(port)  # must not raise
        renames = [op for op in fake.ops if op[1] == "set" and "ifname" in op[2]]
        assert renames and renames[0][2]["ifname"] == "terr0"
        ups = [op for op in fake.ops if op[1] == "set" and op[2].get("state") == "up"]
        assert ups, "interface must still come up after losing the address race"

    def test_stale_terr0_from_prior_attempt_is_replaced(self, monkeypatch) -> None:
        from node_agent import site_lan

        fake = _FakeIPRoute(existing={"sp0000424200": 7, "terr0": 3})
        monkeypatch.setattr(site_lan, "_in_namespace", lambda pid, fn: fn(fake))
        port = site_lan.MemberPort(
            node_id="site-a-gw1",
            pid=111,
            host_ifname="sm0000424200",
            pod_ifname="sp0000424200",
            addresses=("172.16.1.1/24",),
        )
        site_lan._configure_member_pod(port)
        deletes = [op for op in fake.ops if op[1] == "del"]
        assert deletes and deletes[0][2]["index"] == 3
        assert fake.links.get("terr0") == 7

    def test_stale_host_links_are_cleaned_then_recreated(self) -> None:
        from node_agent.site_lan import _ensure_link, _ensure_veth

        fake = _FakeIPRoute(existing={"sl00004242": 5, "sm0000424200": 6})
        idx = _ensure_link(fake, "sl00004242", kind="bridge", mtu=8950)
        assert idx != 5, "stale bridge must be replaced, not adopted"
        assert ("link", "del", {"index": 5}) in fake.ops

        _ensure_veth(fake, "sm0000424200", "sp0000424200", mtu=8950)
        assert ("link", "del", {"index": 6}) in fake.ops
        assert "sm0000424200" in fake.links and "sp0000424200" in fake.links

    def test_unexpected_addr_failure_still_fails_loudly(self, monkeypatch) -> None:
        from node_agent import site_lan

        class _Fake(_FakeIPRoute):
            def addr(self, action, **kwargs):
                raise Exception(13, "Permission denied")

        fake = _Fake(existing={"sp0000424200": 7})
        monkeypatch.setattr(site_lan, "_in_namespace", lambda pid, fn: fn(fake))
        port = site_lan.MemberPort(
            node_id="site-a-gw1",
            pid=111,
            host_ifname="sm0000424200",
            pod_ifname="sp0000424200",
            addresses=("172.16.1.1/24",),
        )
        with pytest.raises(Exception, match="Permission denied"):
            site_lan._configure_member_pod(port)


class _FakeFirewall:
    """Records host-firewall invocations and scripts their return codes."""

    def __init__(self, existing_rules: bool = False, insert_rc: int = 0) -> None:
        self.existing_rules = existing_rules
        self.insert_rc = insert_rc
        self.calls: list[list[str]] = []
        self.deleted: int = 0

    def __call__(self, cmd, capture_output=False, text=False):
        import subprocess

        self.calls.append(list(cmd))
        assert cmd[0] == "nsenter" and cmd[1] == "--net=/proc/1/ns/net", (
            "host firewall state must be mutated in the host netns, not the agent container's"
        )
        action = cmd[3]
        if action == "-C":
            rc = 0 if self.existing_rules else 1
        elif action == "-I":
            rc = self.insert_rc
        elif action == "-D":
            self.deleted += 1
            self.existing_rules = False
            rc = 0
        else:  # pragma: no cover - unexpected verb is a test failure
            raise AssertionError(f"unexpected firewall verb {action}")
        return subprocess.CompletedProcess(cmd, rc, stdout="", stderr="boom")


class TestSiteLanTransitRules:
    """The host-firewall class: br_netfilter feeds bridged site-LAN frames to
    the host FORWARD chain (where e.g. Docker's DROP policy eats them, while
    ARP sails past — the false-healthy LAN). The agent owns its substrate's
    transit: ACCEPT rules pinned for the reserved port namespaces, fail-loud
    when they cannot be installed."""

    def test_rules_pinned_for_both_families_and_both_port_namespaces(self, monkeypatch) -> None:
        from node_agent import site_lan

        fake = _FakeFirewall(existing_rules=False)
        monkeypatch.setattr(site_lan.subprocess, "run", fake)
        site_lan.ensure_site_lan_transit()

        inserts = [c for c in fake.calls if c[3] == "-I"]
        assert {c[2] for c in inserts} == {"iptables", "ip6tables"}
        assert all(c[4] == "FORWARD" and c[5] == "1" for c in inserts)
        assert {c[c.index("--physdev-in") + 1] for c in inserts} == {"sm+", "sv+"}
        assert all("--physdev-is-bridged" in c for c in inserts), (
            "rules must only exempt bridged transit, never routed host traffic"
        )

    def test_present_rules_are_not_duplicated(self, monkeypatch) -> None:
        from node_agent import site_lan

        fake = _FakeFirewall(existing_rules=True)
        monkeypatch.setattr(site_lan.subprocess, "run", fake)
        site_lan.ensure_site_lan_transit()
        assert not [c for c in fake.calls if c[3] == "-I"]

    def test_install_failure_is_loud(self, monkeypatch) -> None:
        from node_agent import site_lan

        fake = _FakeFirewall(existing_rules=False, insert_rc=2)
        monkeypatch.setattr(site_lan.subprocess, "run", fake)
        with pytest.raises(RuntimeError, match="site LAN transit rule"):
            site_lan.ensure_site_lan_transit()

    def test_remove_deletes_until_absent_and_never_raises(self, monkeypatch) -> None:
        from node_agent import site_lan

        fake = _FakeFirewall(existing_rules=True)
        monkeypatch.setattr(site_lan.subprocess, "run", fake)
        site_lan.remove_site_lan_transit()
        assert fake.deleted > 0
