import os
from pathlib import Path

import pytest
from node_agent import ops_events
from node_agent.__main__ import (
    _require_host_ip_for_vxlan_capable_startup,
    _require_ready_fence,
)
from node_agent.command_contract import RuntimeFence, WriterEpochFloor
from node_agent.mpls import module_is_builtin, probe_encapsulation

pytestmark = pytest.mark.usefixtures("_node_agent_ops_spool_path")


def test_startup_rejects_missing_host_ip_in_k8s(monkeypatch: pytest.MonkeyPatch) -> None:
    spooled: list[dict] = []
    monkeypatch.setenv("NODE_NAME", "k3s-a")
    monkeypatch.delenv("HOST_IP", raising=False)
    monkeypatch.setattr(ops_events, "spool_failure", lambda **kwargs: spooled.append(kwargs))

    with pytest.raises(RuntimeError, match="HOST_IP env var is required"):
        _require_host_ip_for_vxlan_capable_startup()

    assert spooled[0]["code"] == "STARTUP_HOST_IP_MISSING"


def test_startup_rejects_invalid_host_ip_in_k8s(monkeypatch: pytest.MonkeyPatch) -> None:
    spooled: list[dict] = []
    monkeypatch.setenv("NODE_NAME", "k3s-a")
    monkeypatch.setenv("HOST_IP", "not-an-ip")
    monkeypatch.setattr(ops_events, "spool_failure", lambda **kwargs: spooled.append(kwargs))

    with pytest.raises(RuntimeError, match="HOST_IP env var is not a valid IP address"):
        _require_host_ip_for_vxlan_capable_startup()

    assert spooled[0]["code"] == "STARTUP_HOST_IP_INVALID"


def test_startup_accepts_valid_host_ip_in_k8s(monkeypatch: pytest.MonkeyPatch) -> None:
    spooled: list[dict] = []
    monkeypatch.setenv("NODE_NAME", "k3s-a")
    monkeypatch.setenv("HOST_IP", "10.0.0.10")
    monkeypatch.setattr(ops_events, "spool_failure", lambda **kwargs: spooled.append(kwargs))

    assert _require_host_ip_for_vxlan_capable_startup() is None
    assert spooled == []


def _mpls_roots(
    tmp_path: Path,
    *,
    tree: bool = True,
    loaded: bool = True,
    builtin_lines: list[str] | None = None,
    inventory: bool = True,
    release: str = "6.8.0-test",
) -> dict:
    """Filesystem fixtures for the two capability probes, laid out as the kernel lays them out."""
    root = Path(tmp_path)
    if tree:
        (root / "proc/sys/net/mpls").mkdir(parents=True)
    else:
        (root / "proc/sys/net").mkdir(parents=True)
    (root / "sys/module").mkdir(parents=True)
    if loaded:
        (root / "sys/module/mpls_iptunnel").mkdir()
    modules = root / "lib/modules" / release
    modules.mkdir(parents=True)
    if inventory:
        (modules / "modules.builtin").write_text("\n".join(builtin_lines or []) + "\n")
    return {
        "proc_root": root / "proc",
        "sys_root": root / "sys",
        "modules_root": root / "lib/modules",
        "release": release,
    }


@pytest.mark.parametrize(
    ("lines", "expected", "detail"),
    [
        (["kernel/net/mpls/mpls_router.ko", "kernel/net/mpls/mpls_iptunnel.ko"], True, "listed"),
        (["kernel/net/mpls/mpls_iptunnel.ko.zst"], True, "listed"),
        (["kernel/net/mpls/mpls_router.ko", "kernel/net/mpls/mpls_gso.ko"], False, "not listed"),
        (["kernel/net/mpls/mpls_iptunnel_extra.ko"], False, "not listed"),
    ],
)
def test_builtin_lookup_reads_the_inventory_contents(tmp_path, lines, expected, detail) -> None:
    inventory = tmp_path / "modules.builtin"
    inventory.write_text("\n".join(lines) + "\n")

    present, why = module_is_builtin("mpls_iptunnel", inventory)

    assert present is expected
    assert why == f"{inventory}: {detail}"


def test_encapsulation_probe_reads_the_running_kernels_inventory(tmp_path) -> None:
    """The inventory read is the one under the running kernel's release directory."""
    roots = _mpls_roots(
        tmp_path,
        loaded=False,
        builtin_lines=["kernel/net/mpls/mpls_iptunnel.ko"],
        release="6.8.0-136-generic",
    )
    other = tmp_path / "lib/modules/6.8.0-999-generic"
    other.mkdir()
    (other / "modules.builtin").write_text("kernel/net/mpls/mpls_router.ko\n")

    probe = probe_encapsulation(
        sys_root=roots["sys_root"], modules_root=roots["modules_root"], release="6.8.0-136-generic"
    )

    assert probe.present
    assert "6.8.0-136-generic/modules.builtin: listed" in probe.detail


def test_encapsulation_probe_defaults_to_the_running_kernel_release(tmp_path) -> None:
    """With no release given, the inventory under ``uname -r`` is the one read."""
    running = os.uname().release
    roots = _mpls_roots(
        tmp_path, loaded=False, builtin_lines=["kernel/net/mpls/mpls_iptunnel.ko"], release=running
    )

    probe = probe_encapsulation(sys_root=roots["sys_root"], modules_root=roots["modules_root"])

    assert probe.present
    assert f"{running}/modules.builtin: listed" in probe.detail


def test_unreadable_builtin_inventory_keeps_its_error_in_the_diagnostic(tmp_path) -> None:
    roots = _mpls_roots(tmp_path, loaded=False, inventory=False)

    probe = probe_encapsulation(
        sys_root=roots["sys_root"], modules_root=roots["modules_root"], release=roots["release"]
    )

    assert not probe.present
    assert "modules.builtin: No such file or directory" in probe.detail
    assert "loaded=no" in probe.detail


def test_ready_fence_missing_identity_fails_before_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[dict] = []
    monkeypatch.setattr(ops_events, "publish", lambda **kwargs: published.append(kwargs))

    with pytest.raises(RuntimeError, match="wiring identity unavailable"):
        _require_ready_fence(
            RuntimeFence(
                session_id="", wiring_generation="", writer_floor=WriterEpochFloor(lambda: None)
            )
        )

    assert published[0]["code"] == "STARTUP_WIRING_IDENTITY_MISSING"


def test_an_unobserved_writer_lease_fails_before_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the writer Lease the epoch floor is unknown: no command is served."""
    from node_agent.__main__ import _require_writer_lease_observed
    from node_agent.writer_lease_view import WriterLeaseView

    published: list[dict] = []
    monkeypatch.setattr(ops_events, "publish", lambda **kwargs: published.append(kwargs))

    with pytest.raises(RuntimeError, match="writer Lease unobserved"):
        _require_writer_lease_observed(WriterLeaseView("nodalarc"))

    assert published[0]["code"] == "STARTUP_WRITER_LEASE_UNOBSERVED"
