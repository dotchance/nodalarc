# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""MPLS kernel support is established before its first use, once per wiring attempt.

A refused node keeps the capability diagnostic as its failure and receives no
MPLS sysctl write anywhere: not in the sysctl loop, not on its ISL interfaces,
not on its ground interfaces. Everything else about the attempt is unchanged.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from nodalarc.substrate.manifest_contract import REQUIRED_WIRING_PHASES, WiringManifest
from node_agent.mpls import CapabilityProbe, ModuleLoad, MplsSupport
from node_agent.pid_discovery import NamespaceHandle
from node_agent.wiring import execute_wiring

LOCAL_NODE = "node02"
MPLS_SYSCTLS = {"net.ipv4.ip_forward": "1", "net.mpls.platform_labels": "1048575"}


def _manifest(*, mpls: bool = True) -> WiringManifest:
    """Two MPLS satellites joined by one ISL, one MPLS ground station with one ground
    interface, and one satellite that never asked for MPLS."""
    sysctls = MPLS_SYSCTLS if mpls else {"net.ipv4.ip_forward": "1"}
    satellite = {
        "node_type": "satellite",
        "host": LOCAL_NODE,
        "sysctls": dict(sysctls),
        "gnd_interfaces": [],
        "mpls_enable": mpls,
        "segment_routing": False,
        "mtu": 1500,
        "remove_default_route": False,
        "plane": 0,
    }
    nodes = {
        "sat-a": {
            **satellite,
            "slot": 0,
            "isl_interfaces": [{"name": "isl0", "peer_node": "sat-b", "peer_iface": "isl0"}],
        },
        "sat-b": {
            **satellite,
            "slot": 1,
            "isl_interfaces": [{"name": "isl0", "peer_node": "sat-a", "peer_iface": "isl0"}],
        },
        "sat-c": {
            **satellite,
            "slot": 2,
            "sysctls": {"net.ipv4.ip_forward": "1"},
            "mpls_enable": False,
            "isl_interfaces": [],
        },
        "gs-x": {
            "node_type": "ground_station",
            "host": LOCAL_NODE,
            "gs_name": "gs-x",
            "gs_index": 0,
            "sysctls": dict(sysctls),
            "isl_interfaces": [],
            "gnd_interfaces": [{"name": "gnd0"}],
            "mpls_enable": mpls,
            "segment_routing": False,
            "mtu": 1500,
            "remove_default_route": False,
        },
    }
    return WiringManifest.model_validate(
        {
            "session_id": "test-session",
            "session_run_id": "run-test-0001",
            "owner_uid": "owner-uid-1",
            "wiring_generation": "sha256:" + "a" * 64,
            "required_phases": list(REQUIRED_WIRING_PHASES),
            "nodes": nodes,
            "ground_bridges": {"gs-x": {}},
            "site_lans": {},
            "required_substrate_pairs": [],
            "isl_link_count": 1,
        }
    )


def _handles(*, mpls: bool = True) -> dict[str, NamespaceHandle]:
    return {
        node_id: NamespaceHandle(
            node_id=node_id,
            pod_uid=f"pod-{node_id}",
            sandbox_id=f"sb-{node_id}",
            sandbox_attempt=0,
            pid=4000 + index,
            netns_id=f"40265321{index:02d}",
            mpls_enable=mpls and node_id != "sat-c",
        )
        for index, node_id in enumerate(("sat-a", "sat-b", "sat-c", "gs-x"))
    }


def _support(*, available: bool) -> MplsSupport:
    encapsulation = CapabilityProbe(
        "mpls encapsulation",
        "mpls_iptunnel",
        available,
        "loaded=no per /sys/module/mpls_iptunnel; builtin=no per /lib/modules/6.8.0-test/modules.builtin: not listed",
    )
    return MplsSupport(
        routing=CapabilityProbe("mpls routing", "/proc/sys/net/mpls", True),
        encapsulation=encapsulation,
        modules=(
            ModuleLoad("mpls_router", True, 0, ""),
            ModuleLoad(
                "mpls_iptunnel",
                True,
                1,
                "modprobe: ERROR: could not insert 'mpls_iptunnel': Operation not permitted\n",
            ),
        ),
    )


class _Run:
    """One execute_wiring call with every kernel-touching phase replaced by recorders."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, support: MplsSupport | None) -> None:
        monkeypatch.setenv("NODE_NAME", LOCAL_NODE)
        self.calls: list[tuple] = []
        self.support = support

    def __enter__(self) -> _Run:
        calls = self.calls

        def sysctl(pid, key, value, already_in_ns=False):
            calls.append(("sysctl", pid, key, value))
            return None

        def mpls_input(pid, ifname):
            calls.append(("enable_mpls_input", pid, ifname))

        def check(**_kwargs):
            calls.append(("ensure_mpls_kernel_support",))
            return self.support

        self._patches = [
            patch("node_agent.wiring._cleanup_stale_interfaces", lambda *a, **k: None),
            patch("node_agent.wiring._write_sysctl_in_netns", sysctl),
            patch("node_agent.wiring.enable_mpls_input", mpls_input),
            patch(
                "node_agent.wiring.create_mediated_isl",
                lambda *a, **k: calls.append(("create_mediated_isl", a[2], a[3])),
            ),
            patch(
                "node_agent.wiring.create_ground_bridge",
                lambda *a, **k: calls.append(("create_ground_bridge", k.get("ifname"))),
            ),
            patch(
                "node_agent.wiring.create_satellite_ground_veth",
                lambda *a, **k: calls.append(("create_satellite_ground_veth",)),
            ),
            patch("node_agent.wiring.configure_interface", lambda *a, **k: None),
            patch("node_agent.wiring.finalize_pod_network", lambda *a, **k: (None, None)),
            patch("node_agent.wiring.ensure_mpls_kernel_support", check),
            # execute_wiring loads the in-cluster config once for its progress
            # writes; outside a cluster those writes are replaced by a stub client.
            patch("node_agent.wiring.kubernetes.config.load_incluster_config", lambda: None),
            patch("node_agent.wiring.kubernetes.client.CoreV1Api", lambda: SimpleNamespace()),
        ]
        for item in self._patches:
            item.start()
        return self

    def __exit__(self, *exc) -> None:
        for item in self._patches:
            item.stop()

    def wire(self, manifest: WiringManifest, *, mpls: bool = True):
        return execute_wiring(manifest, namespace="testns", handles=_handles(mpls=mpls))


def test_support_check_runs_once_before_the_first_sysctl_write(monkeypatch) -> None:
    with _Run(monkeypatch, _support(available=True)) as run:
        run.wire(_manifest())

    kinds = [call[0] for call in run.calls]
    assert kinds.count("ensure_mpls_kernel_support") == 1
    assert kinds.index("ensure_mpls_kernel_support") < kinds.index("sysctl")


def test_unavailable_support_refuses_every_mpls_node_and_writes_no_mpls_sysctl(monkeypatch) -> None:
    with _Run(monkeypatch, _support(available=False)) as run:
        statuses = run.wire(_manifest())

    for node_id in ("sat-a", "sat-b", "gs-x"):
        status = statuses[node_id]
        assert status.status != "ready", node_id
        mpls_phase = next(phase for phase in status.phases if phase.phase == "mpls")
        # Every recorded wiring failure reports through the existing failure path,
        # which marks the node dirty_kernel; the refusal changes nothing there.
        assert status.status == "dirty_kernel", (node_id, status)
        assert status.dirty_kernel is True
        assert mpls_phase.status == "dirty_kernel", (node_id, mpls_phase)
        message = mpls_phase.error_message
        assert message.startswith("MPLS kernel support unavailable: "), message
        assert "mpls encapsulation absent" in message
        assert "mpls_iptunnel: modprobe rc=1" in message
        assert "Operation not permitted" in message
    assert statuses["sat-c"].status == "ready"

    mpls_writes = [c for c in run.calls if c[0] == "sysctl" and c[2].startswith("net.mpls.")]
    assert mpls_writes == []
    assert [c for c in run.calls if c[0] == "enable_mpls_input"] == []
    # unrelated behavior unchanged: the forwarding sysctl, the ISL and the ground bridge still happen
    assert ("sysctl", 4002, "net.ipv4.ip_forward", "1") in run.calls
    assert ("sysctl", 4000, "net.ipv4.ip_forward", "1") in run.calls
    assert ("create_mediated_isl", "isl0", "isl0") in run.calls
    assert ("create_ground_bridge", "gnd0") in run.calls


def test_available_support_after_a_failed_modprobe_proceeds_with_every_mpls_write(
    monkeypatch,
) -> None:
    """The encapsulation module would not load but the kernel has the capability built in."""
    with _Run(monkeypatch, _support(available=True)) as run:
        statuses = run.wire(_manifest())

    assert all(status.status == "ready" for status in statuses.values()), statuses
    assert ("sysctl", 4000, "net.mpls.platform_labels", "1048575") in run.calls
    assert ("sysctl", 4003, "net.mpls.platform_labels", "1048575") in run.calls
    enables = sorted(c[1:] for c in run.calls if c[0] == "enable_mpls_input")
    assert enables == [(4000, "isl0"), (4001, "isl0"), (4003, "gnd0")]


def test_wiring_without_an_mpls_node_never_checks(monkeypatch) -> None:
    with _Run(monkeypatch, None) as run:
        statuses = run.wire(_manifest(mpls=False), mpls=False)

    assert all(status.status == "ready" for status in statuses.values())
    assert [c for c in run.calls if c[0] == "ensure_mpls_kernel_support"] == []
    assert [c for c in run.calls if c[0] == "sysctl" and c[2].startswith("net.mpls.")] == []


def test_every_wiring_attempt_checks_the_kernel_again(monkeypatch) -> None:
    """No initialized-state cache: two attempts in one process read the kernel twice."""
    with _Run(monkeypatch, _support(available=True)) as run:
        run.wire(_manifest())
        run.wire(_manifest())

    assert [c[0] for c in run.calls].count("ensure_mpls_kernel_support") == 2
