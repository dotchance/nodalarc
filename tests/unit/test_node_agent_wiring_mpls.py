# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""MPLS at wiring: kernel support before first use, then per-interface MPLS input
owned by the operation that creates the interface and read back before ready.

A refused node keeps the capability diagnostic as its failure and receives no
MPLS sysctl write anywhere. An interface this attempt created is written and
read back; one it reused is read and a mismatch refused; an entry whose peer is
on another host is never touched here. Every MPLS failure lands under the
``mpls`` phase; everything else about the attempt is unchanged.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from nodalarc.substrate.manifest_contract import REQUIRED_WIRING_PHASES, WiringManifest
from node_agent.ground_bridge import MediatedIsl, PodVeth
from node_agent.kernel_verifier import KernelStateConflict, Proof
from node_agent.mpls import CapabilityProbe, ModuleLoad, MplsInputError, MplsSupport
from node_agent.pid_discovery import NamespaceHandle
from node_agent.wiring import execute_wiring

LOCAL_NODE = "node02"
MPLS_SYSCTLS = {"net.ipv4.ip_forward": "1", "net.mpls.platform_labels": "1048575"}


def _manifest(
    *, mpls: bool = True, cross_peer: bool = False, site_lan: bool = False
) -> WiringManifest:
    """Two MPLS satellites joined by one local ISL, one MPLS ground station with one
    ground interface, one satellite that never asked for MPLS, optionally a peer
    on another host reachable only through a cross-host ISL, and optionally a
    site LAN joining the MPLS ground station with a second one that never asked
    for MPLS."""
    sysctls = MPLS_SYSCTLS if mpls else {"net.ipv4.ip_forward": "1"}
    satellite = {
        "node_type": "satellite",
        "host": LOCAL_NODE,
        "sysctls": dict(sysctls),
        "gnd_interfaces": [],
        "mpls_enable": mpls,
        "segment_routing": False,
        "remove_default_route": False,
        "plane": 0,
    }
    sat_a_isls = [{"name": "isl0", "peer_node": "sat-b", "peer_iface": "isl0"}]
    if cross_peer:
        sat_a_isls.append({"name": "isl1", "peer_node": "sat-d", "peer_iface": "isl0"})
    nodes = {
        "sat-a": {**satellite, "slot": 0, "isl_interfaces": sat_a_isls},
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
            "remove_default_route": False,
        },
    }
    ground_bridges: dict[str, dict] = {"gs-x": {}}
    site_lans: dict[str, dict] = {}
    if site_lan:
        nodes["gs-y"] = {
            **nodes["gs-x"],
            "gs_name": "gs-y",
            "gs_index": 1,
            "sysctls": {"net.ipv4.ip_forward": "1"},
            "mpls_enable": False,
        }
        ground_bridges["gs-y"] = {}
        site_lans["site-a-lan0"] = {
            "vni": 4242,
            "members": [
                {
                    "node_id": "gs-x",
                    "interface": "terr0",
                    "addresses": ["172.16.1.1/24"],
                    "gateways": [],
                    "k3s_node": LOCAL_NODE,
                    "host_ip": "192.0.2.2",
                },
                {
                    "node_id": "gs-y",
                    "interface": "terr0",
                    "addresses": ["172.16.1.2/24"],
                    "gateways": [],
                    "k3s_node": LOCAL_NODE,
                    "host_ip": "192.0.2.2",
                },
            ],
        }
    if cross_peer:
        nodes["sat-d"] = {
            **satellite,
            "host": "node03",
            "slot": 3,
            "isl_interfaces": [{"name": "isl0", "peer_node": "sat-a", "peer_iface": "isl1"}],
        }
    return WiringManifest.model_validate(
        {
            "session_id": "test-session",
            "session_run_id": "run-test-0001",
            "owner_uid": "owner-uid-1",
            "wiring_generation": "sha256:" + "a" * 64,
            "required_phases": list(REQUIRED_WIRING_PHASES),
            "nodes": nodes,
            "ground_bridges": ground_bridges,
            "site_lans": site_lans,
            "required_substrate_pairs": [],
            "isl_link_count": 2 if cross_peer else 1,
        }
    )


def _handles(*, mpls: bool = True, site_lan: bool = False) -> dict[str, NamespaceHandle]:
    node_ids = ("sat-a", "sat-b", "sat-c", "gs-x") + (("gs-y",) if site_lan else ())
    return {
        node_id: NamespaceHandle(
            node_id=node_id,
            pod_uid=f"pod-{node_id}",
            sandbox_id=f"sb-{node_id}",
            sandbox_attempt=0,
            pid=4000 + index,
            netns_id=f"40265321{index:02d}",
            mpls_enable=mpls and node_id not in ("sat-c", "gs-y"),
        )
        for index, node_id in enumerate(node_ids)
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


def _phase(status, name: str):
    return next(phase for phase in status.phases if phase.phase == name)


class _Run:
    """One execute_wiring call with every kernel-touching phase replaced by recorders.

    ``created`` is the mediated ISL creator's answer for (end a, end b);
    ``ground_created`` the ground creators' answer; ``configure_failures`` maps
    (pid, ifname) to the exception the MPLS input step raises there;
    ``ground_creator_failure`` makes the ground bridge creator itself fail;
    ``site_wiring_failure`` makes the site LAN creator fail for every site.
    """

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        support: MplsSupport | None,
        *,
        created: tuple[bool, bool] = (True, True),
        ground_created: bool = True,
        configure_failures: dict[tuple[int, str], Exception] | None = None,
        ground_creator_failure: Exception | None = None,
        site_wiring_failure: Exception | None = None,
    ) -> None:
        monkeypatch.setenv("NODE_NAME", LOCAL_NODE)
        monkeypatch.setenv("HOST_IP", "192.0.2.2")
        self.calls: list[tuple] = []
        self.support = support
        self.created = created
        self.ground_created = ground_created
        self.configure_failures = configure_failures or {}
        self.ground_creator_failure = ground_creator_failure
        self.site_wiring_failure = site_wiring_failure

    def __enter__(self) -> _Run:
        calls = self.calls
        run = self

        def sysctl(pid, key, value, already_in_ns=False):
            calls.append(("sysctl", pid, key, value))
            return None

        def configure(pid, ifname, *, created, subject):
            calls.append(("configure_mpls_input", pid, ifname, created, subject))
            failure = run.configure_failures.get((pid, ifname))
            if failure is not None:
                raise failure
            return Proof.ok(f"mpls input enabled on {ifname}", f"device={ifname}", "observed=1")

        def check(**_kwargs):
            calls.append(("ensure_mpls_kernel_support",))
            return run.support

        def mediated(pid_a, pid_b, ifname_a, ifname_b, *, node_id_a, node_id_b):
            calls.append(("create_mediated_isl", node_id_a, ifname_a, node_id_b, ifname_b))
            return MediatedIsl("host-a", "host-b", run.created[0], run.created[1])

        def ground_bridge(gs_id, gs_pid, *, ifname):
            calls.append(("create_ground_bridge", gs_id, ifname))
            if run.ground_creator_failure is not None:
                raise run.ground_creator_failure
            return PodVeth(f"_gbr-{gs_id}", ifname, created=run.ground_created)

        def sat_ground(node_id, pid, *, ifname):
            calls.append(("create_satellite_ground_veth", node_id, ifname))
            return PodVeth(f"_gnd-{node_id}", ifname, created=run.ground_created)

        def site_wire(plan):
            calls.append(
                ("wire_site_lan", plan.site_id, tuple(p.node_id for p in plan.local_members))
            )
            if run.site_wiring_failure is not None:
                raise run.site_wiring_failure

        self._patches = [
            patch("node_agent.wiring._cleanup_stale_interfaces", lambda *a, **k: None),
            patch("node_agent.wiring._write_sysctl_in_netns", sysctl),
            patch("node_agent.wiring.configure_mpls_input", configure),
            patch("node_agent.wiring.create_mediated_isl", mediated),
            patch("node_agent.wiring.create_ground_bridge", ground_bridge),
            patch("node_agent.wiring.create_satellite_ground_veth", sat_ground),
            patch("node_agent.wiring.configure_interface", lambda *a, **k: None),
            patch("node_agent.wiring.finalize_pod_network", lambda *a, **k: (None, None)),
            patch("node_agent.wiring.ensure_mpls_kernel_support", check),
            patch("node_agent.site_lan.wire_site_lan", site_wire),
            patch(
                "node_agent.site_lan.ensure_site_lan_transit",
                lambda: calls.append(("ensure_site_lan_transit",)),
            ),
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

    def wire(self, manifest: WiringManifest, *, mpls: bool = True, site_lan: bool = False):
        return execute_wiring(
            manifest, namespace="testns", handles=_handles(mpls=mpls, site_lan=site_lan)
        )

    def configured(self) -> list[tuple]:
        return [c[1:] for c in self.calls if c[0] == "configure_mpls_input"]


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
        # Every recorded wiring failure reports through the existing failure path,
        # which marks the node dirty_kernel; the refusal changes nothing there.
        assert status.status == "dirty_kernel", (node_id, status)
        assert status.dirty_kernel is True
        mpls_phase = _phase(status, "mpls")
        assert mpls_phase.status == "dirty_kernel", (node_id, mpls_phase)
        message = mpls_phase.error_message
        assert message.startswith("MPLS kernel support unavailable: "), message
        assert "mpls encapsulation absent" in message
        assert "mpls_iptunnel: modprobe rc=1" in message
        assert "Operation not permitted" in message
    assert statuses["sat-c"].status == "ready"

    mpls_writes = [c for c in run.calls if c[0] == "sysctl" and c[2].startswith("net.mpls.")]
    assert mpls_writes == []
    assert run.configured() == []
    # unrelated behavior unchanged: the forwarding sysctl, the ISL and the ground bridge still happen
    assert ("sysctl", 4002, "net.ipv4.ip_forward", "1") in run.calls
    assert ("sysctl", 4000, "net.ipv4.ip_forward", "1") in run.calls
    assert ("create_mediated_isl", "sat-a", "isl0", "sat-b", "isl0") in run.calls
    assert ("create_ground_bridge", "gs-x", "gnd0") in run.calls


def test_created_interfaces_are_configured_and_read_back_before_ready(monkeypatch) -> None:
    """Both ends of the created ISL and the created ground interface get the MPLS
    input step with the creator's decision; the node without MPLS gets nothing."""
    with _Run(monkeypatch, _support(available=True)) as run:
        statuses = run.wire(_manifest())

    assert all(status.status == "ready" for status in statuses.values()), statuses
    assert sorted(run.configured()) == [
        (4000, "isl0", True, "ISL sat-a/isl0"),
        (4001, "isl0", True, "ISL sat-b/isl0"),
        (4003, "gnd0", True, "ground gs-x/gnd0"),
    ]
    assert ("sysctl", 4000, "net.mpls.platform_labels", "1048575") in run.calls
    assert ("sysctl", 4003, "net.mpls.platform_labels", "1048575") in run.calls
    kinds = [call[0] for call in run.calls]
    assert kinds.index("create_mediated_isl") < kinds.index("configure_mpls_input")


def test_reused_interfaces_are_verified_with_the_creators_decision(monkeypatch) -> None:
    with _Run(
        monkeypatch, _support(available=True), created=(False, True), ground_created=False
    ) as run:
        statuses = run.wire(_manifest())

    assert all(status.status == "ready" for status in statuses.values())
    assert sorted(run.configured()) == [
        (4000, "isl0", False, "ISL sat-a/isl0"),
        (4001, "isl0", True, "ISL sat-b/isl0"),
        (4003, "gnd0", False, "ground gs-x/gnd0"),
    ]


def test_cross_host_isl_entries_are_never_touched_at_wiring(monkeypatch) -> None:
    """sat-a's isl1 leads to a peer on another host: no interface exists yet, so no
    MPLS write, no read and no failure; the local pair still gets its step."""
    with _Run(monkeypatch, _support(available=True)) as run:
        statuses = run.wire(_manifest(cross_peer=True))

    assert statuses["sat-a"].status == "ready"
    assert [c for c in run.calls if c[0] == "create_mediated_isl"] == [
        ("create_mediated_isl", "sat-a", "isl0", "sat-b", "isl0")
    ]
    assert (4000, "isl1") not in {(pid, ifname) for pid, ifname, *_ in run.configured()}
    assert (4000, "isl0", True, "ISL sat-a/isl0") in run.configured()


@pytest.mark.parametrize(
    ("failure", "expected_text"),
    [
        (
            MplsInputError("ISL sat-a/isl0", "isl0", "write failed: [Errno 2] No such file"),
            "write failed: [Errno 2] No such file",
        ),
        (
            MplsInputError(
                "ISL sat-a/isl0",
                "isl0",
                "written but not read back: mpls input disabled on isl0",
                ("device=isl0", "expected=1", "observed=0"),
            ),
            "written but not read back: mpls input disabled on isl0 [device=isl0, expected=1, observed=0]",
        ),
        (
            KernelStateConflict(
                "ISL sat-a/isl0",
                ("isl0@pod",),
                ("mpls input disabled on isl0",),
                (("device=isl0", "expected=1", "observed=0"),),
            ),
            "existing kernel state is not the requested link (present: isl0@pod; failed: mpls input disabled on isl0 [device=isl0, expected=1, observed=0])",
        ),
    ],
    ids=["write-failed", "written-but-read-back-zero", "reused-mismatch-refused"],
)
def test_isl_mpls_step_failures_land_under_the_mpls_phase(
    monkeypatch, failure, expected_text
) -> None:
    with _Run(
        monkeypatch, _support(available=True), configure_failures={(4000, "isl0"): failure}
    ) as run:
        statuses = run.wire(_manifest())

    failed = statuses["sat-a"]
    assert failed.status == "dirty_kernel"
    mpls_phase = _phase(failed, "mpls")
    assert mpls_phase.status == "dirty_kernel"
    assert expected_text in mpls_phase.error_message, mpls_phase.error_message
    assert _phase(failed, "isl_interfaces").status == "ready"
    assert statuses["sat-b"].status == "ready"
    assert statuses["gs-x"].status == "ready"


def test_ground_mpls_step_failure_is_mpls_and_ground_creator_failure_stays_ground(
    monkeypatch,
) -> None:
    failure = MplsInputError("ground gs-x/gnd0", "gnd0", "write failed: Operation not permitted")
    with _Run(
        monkeypatch, _support(available=True), configure_failures={(4003, "gnd0"): failure}
    ) as run:
        statuses = run.wire(_manifest())
    gs = statuses["gs-x"]
    assert gs.status == "dirty_kernel"
    assert _phase(gs, "mpls").status == "dirty_kernel"
    assert "write failed: Operation not permitted" in _phase(gs, "mpls").error_message
    # The failure is attributed to mpls, not to the ground creation.
    assert _phase(gs, "ground_infrastructure").error_message == ""
    assert _phase(gs, "ground_infrastructure").status != "dirty_kernel"

    with _Run(
        monkeypatch,
        _support(available=True),
        ground_creator_failure=RuntimeError("veth add failed"),
    ) as run:
        statuses = run.wire(_manifest())
    gs = statuses["gs-x"]
    assert _phase(gs, "ground_infrastructure").status == "dirty_kernel"
    assert "veth add failed" in _phase(gs, "ground_infrastructure").error_message
    assert (4003, "gnd0") not in {(pid, ifname) for pid, ifname, *_ in run.configured()}


def test_wiring_without_an_mpls_node_never_checks_or_configures(monkeypatch) -> None:
    with _Run(monkeypatch, None) as run:
        statuses = run.wire(_manifest(mpls=False), mpls=False)

    assert all(status.status == "ready" for status in statuses.values())
    assert [c for c in run.calls if c[0] == "ensure_mpls_kernel_support"] == []
    assert [c for c in run.calls if c[0] == "sysctl" and c[2].startswith("net.mpls.")] == []
    assert run.configured() == []


def test_every_wiring_attempt_checks_the_kernel_again(monkeypatch) -> None:
    """No initialized-state cache: two attempts in one process read the kernel twice."""
    with _Run(monkeypatch, _support(available=True)) as run:
        run.wire(_manifest())
        run.wire(_manifest())

    assert [c[0] for c in run.calls].count("ensure_mpls_kernel_support") == 2


_SITE_STEP = ("configure_mpls_input", 4003, "terr0", True, "site LAN site-a-lan0/gs-x/terr0")
_SITE_WIRED = ("wire_site_lan", "site-a-lan0", ("gs-x", "gs-y"))


def test_site_lan_member_interfaces_are_configured_after_the_site_is_wired(monkeypatch) -> None:
    """The site LAN creator recreates every member veth, so each MPLS member's
    interface gets the input step as created, after the site is wired; the member
    whose node never asked for MPLS is not touched."""
    with _Run(monkeypatch, _support(available=True)) as run:
        statuses = run.wire(_manifest(site_lan=True), site_lan=True)

    assert all(status.status == "ready" for status in statuses.values()), statuses
    assert _SITE_STEP in run.calls
    assert (4004, "terr0") not in {(pid, ifname) for pid, ifname, *_ in run.configured()}
    assert run.calls.index(_SITE_WIRED) < run.calls.index(_SITE_STEP)


def test_refused_member_gets_no_site_lan_mpls_step(monkeypatch) -> None:
    with _Run(monkeypatch, _support(available=False)) as run:
        statuses = run.wire(_manifest(site_lan=True), site_lan=True)

    assert _SITE_WIRED in run.calls
    assert run.configured() == []
    assert _phase(statuses["gs-x"], "mpls").status == "dirty_kernel"
    assert statuses["gs-y"].status == "ready"


def test_site_lan_mpls_step_failure_lands_under_mpls_for_that_member_only(monkeypatch) -> None:
    failure = MplsInputError(
        "site LAN site-a-lan0/gs-x/terr0",
        "terr0",
        "written but not read back: mpls input disabled on terr0",
        ("device=terr0", "expected=1", "observed=0"),
    )
    with _Run(
        monkeypatch, _support(available=True), configure_failures={(4003, "terr0"): failure}
    ) as run:
        statuses = run.wire(_manifest(site_lan=True), site_lan=True)

    gs = statuses["gs-x"]
    assert gs.status == "dirty_kernel"
    assert _phase(gs, "mpls").status == "dirty_kernel"
    assert (
        "written but not read back: mpls input disabled on terr0"
        " [device=terr0, expected=1, observed=0]"
    ) in _phase(gs, "mpls").error_message
    assert _phase(gs, "terrestrial_interfaces").status != "dirty_kernel"
    assert _phase(gs, "terrestrial_interfaces").error_message == ""
    assert statuses["gs-y"].status == "ready"
    # The step neither re-wires nor tears the site down.
    assert [c for c in run.calls if c[0] == "wire_site_lan"] == [_SITE_WIRED]


def test_site_lan_wiring_failure_stays_terrestrial_and_runs_no_mpls_step(monkeypatch) -> None:
    with _Run(
        monkeypatch,
        _support(available=True),
        site_wiring_failure=RuntimeError("bridge add failed"),
    ) as run:
        statuses = run.wire(_manifest(site_lan=True), site_lan=True)

    for node_id in ("gs-x", "gs-y"):
        phase = _phase(statuses[node_id], "terrestrial_interfaces")
        assert phase.status == "dirty_kernel", (node_id, phase)
        assert "bridge add failed" in phase.error_message
    assert (4003, "terr0") not in {(pid, ifname) for pid, ifname, *_ in run.configured()}
    # The ground interface's own step happened earlier and keeps its attribution.
    assert (4003, "gnd0", True, "ground gs-x/gnd0") in run.configured()
