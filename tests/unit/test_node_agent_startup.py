import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from node_agent import ops_events
from node_agent.__main__ import (
    _require_host_ip_for_vxlan_capable_startup,
    _require_ready_fence,
)
from node_agent.command_contract import RuntimeFence
from node_agent.mpls import ensure_mpls_kernel_support, module_is_builtin, probe_encapsulation


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


def _modprobe(monkeypatch: pytest.MonkeyPatch, answers: dict[str, tuple[int, str]]) -> list[str]:
    calls: list[str] = []

    class Result:
        def __init__(self, returncode: int, stderr: str) -> None:
            self.returncode = returncode
            self.stderr = stderr

    def run(cmd, **_kwargs):
        calls.append(cmd[1])
        returncode, stderr = answers[cmd[1]]
        return Result(returncode, stderr)

    monkeypatch.setattr("node_agent.mpls.subprocess.run", run)
    return calls


def _events(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    published: list[dict] = []
    monkeypatch.setattr(ops_events, "publish", lambda **kwargs: published.append(kwargs))
    return published


def test_both_modules_load_and_both_capabilities_present(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("NODE_NAME", "k3s-a")
    calls = _modprobe(monkeypatch, {"mpls_router": (0, ""), "mpls_iptunnel": (0, "")})
    published = _events(monkeypatch)

    support = ensure_mpls_kernel_support(**_mpls_roots(tmp_path))

    assert calls == ["mpls_router", "mpls_iptunnel"]
    assert support.available
    assert support.routing.present and support.encapsulation.present
    assert all(module.attempted and module.returncode == 0 for module in support.modules)
    assert published == []
    assert "mpls routing present" in support.diagnostic()
    assert "mpls encapsulation present" in support.diagnostic()


def test_failed_modprobe_with_its_capability_present_is_built_in_not_a_refusal(
    monkeypatch, tmp_path, caplog
) -> None:
    """mpls_iptunnel will not load as a module but the running kernel lists it as built in:
    the capability is present, so no refusal and no event; the stderr stays as a diagnostic."""
    monkeypatch.setenv("NODE_NAME", "k3s-a")
    _modprobe(
        monkeypatch,
        {
            "mpls_router": (0, ""),
            "mpls_iptunnel": (1, "modprobe: FATAL: Module mpls_iptunnel not found"),
        },
    )
    published = _events(monkeypatch)
    roots = _mpls_roots(tmp_path, loaded=False, builtin_lines=["kernel/net/mpls/mpls_iptunnel.ko"])

    with caplog.at_level("INFO", logger="node_agent.mpls"):
        support = ensure_mpls_kernel_support(**roots)

    assert support.available
    assert support.encapsulation.present
    assert "builtin=yes" in support.encapsulation.detail
    assert published == []
    assert "Module mpls_iptunnel not found" in caplog.text
    assert "rc=1" in support.diagnostic()


def test_routing_present_but_encapsulation_neither_loaded_nor_built_in_is_unavailable(
    monkeypatch, tmp_path
) -> None:
    """The sysctl tree proves routing only; a failed mpls_iptunnel load with the module
    neither loaded nor built in is a genuine capability failure with its stderr kept."""
    monkeypatch.setenv("NODE_NAME", "k3s-a")
    _modprobe(
        monkeypatch, {"mpls_router": (0, ""), "mpls_iptunnel": (1, "Operation not permitted")}
    )
    published = _events(monkeypatch)
    roots = _mpls_roots(tmp_path, loaded=False, builtin_lines=["kernel/net/mpls/mpls_router.ko"])

    support = ensure_mpls_kernel_support(**roots)

    assert not support.available
    assert support.routing.present and not support.encapsulation.present
    assert [event["details"]["module"] for event in published] == ["mpls_iptunnel"]
    assert published[0]["code"] == "STARTUP_KERNEL_MODULE_UNAVAILABLE"
    assert published[0]["details"]["stderr"] == "Operation not permitted"
    assert published[0]["details"]["returncode"] == 1
    diagnostic = support.diagnostic()
    assert "mpls encapsulation absent" in diagnostic
    assert "loaded=no" in diagnostic and "builtin=no" in diagnostic
    assert "mpls_iptunnel: modprobe rc=1 stderr='Operation not permitted'" in diagnostic


def test_routing_tree_absent_is_unavailable_even_when_modprobe_succeeds(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("NODE_NAME", "k3s-a")
    _modprobe(monkeypatch, {"mpls_router": (0, ""), "mpls_iptunnel": (0, "")})
    published = _events(monkeypatch)

    support = ensure_mpls_kernel_support(**_mpls_roots(tmp_path, tree=False))

    assert not support.available
    assert not support.routing.present
    assert "mpls routing absent" in support.diagnostic()
    assert "proc/sys/net/mpls" in support.diagnostic()
    assert published == []


def test_outside_kubernetes_no_modprobe_is_attempted_and_the_probes_decide(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv("NODE_NAME", raising=False)
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    calls = _modprobe(monkeypatch, {"mpls_router": (0, ""), "mpls_iptunnel": (0, "")})

    support = ensure_mpls_kernel_support(**_mpls_roots(tmp_path))

    assert calls == []
    assert support.available
    assert all(not module.attempted for module in support.modules)
    assert "modprobe not attempted outside Kubernetes" in support.diagnostic()


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
        _require_ready_fence(RuntimeFence(session_id="", wiring_generation=""))

    assert published[0]["code"] == "STARTUP_WIRING_IDENTITY_MISSING"


@pytest.mark.parametrize("shutdown", ["monitor_failure", "sigterm", "subscribe_failure", "cancel"])
def test_agent_shutdown_joins_wiring_before_closing_nats(shutdown):
    """Run the real entry point and executor in a child so a stuck worker fails safely."""
    script = textwrap.dedent(
        """
        import asyncio
        import os
        import signal
        import sys
        import threading
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch

        import kubernetes.client
        import kubernetes.config
        import nodal.logging
        import nodalarc.platform_config
        from node_agent import __main__ as agent, substrate_monitor

        shutdown = sys.argv[1]
        sys.argv = ["node-agent"]
        finished = threading.Event()
        closed = []
        v1 = MagicMock()
        manifest = SimpleNamespace(
            session_id="test-session", wiring_generation="sha256:" + "a" * 64,
            nodes={"sat-a": {}}, site_lans={},
        )
        nc = AsyncMock()
        sub = AsyncMock()
        nc.subscribe.return_value = sub
        if shutdown == "subscribe_failure":
            nc.subscribe.side_effect = RuntimeError("subscription failed")

        async def close():
            assert finished.is_set(), "NATS closed while the wiring watcher still ran"
            closed.append(True)
        nc.close.side_effect = close

        async def monitor(hostname):
            if shutdown == "monitor_failure":
                raise RuntimeError("substrate publication failed")
            asyncio.get_running_loop().call_soon(os.kill, os.getpid(), signal.SIGTERM)
            await asyncio.Event().wait()

        async def run():
            loop = asyncio.get_running_loop()
            submit = loop.run_in_executor

            def record_worker(executor, operation, *args):
                if getattr(operation, "__name__", None) == "_wiring_watcher":
                    def watch():
                        try:
                            return operation(*args)
                        finally:
                            finished.set()
                    return submit(executor, watch)
                return submit(executor, operation, *args)

            loop.run_in_executor = record_worker
            task = asyncio.create_task(agent.main())

            def read(name, namespace):
                if shutdown == "cancel":
                    loop.call_soon_threadsafe(task.cancel)
                    raise kubernetes.client.rest.ApiException(status=404)
                return SimpleNamespace(metadata=SimpleNamespace(resource_version="1"),
                                       data={"manifest.json": "{}"})
            v1.read_namespaced_config_map.side_effect = read
            try:
                await task
            except asyncio.CancelledError:
                assert shutdown == "cancel"
            except RuntimeError as exc:
                expected = {"monitor_failure": "substrate publication failed",
                            "subscribe_failure": "subscription failed"}
                assert str(exc) == expected[shutdown], str(exc)
            else:
                assert shutdown == "sigterm", "fatal startup or monitor error was suppressed"
            assert finished.is_set()
            assert closed == [True]
            if shutdown in ("monitor_failure", "sigterm"):
                sub.unsubscribe.assert_awaited_once()

        os.environ["HOST_IP"] = "10.0.0.1"
        with (
            patch.object(nodal.logging, "configure"),
            patch.object(nodal.logging, "connect", new=AsyncMock()),
            patch.object(nodalarc.platform_config, "init_platform_config"),
            patch.object(nodalarc.platform_config, "get_platform_config",
                         return_value=SimpleNamespace(kubernetes_namespace="nodalarc")),
            patch.object(kubernetes.config, "load_incluster_config"),
            patch.object(kubernetes.client, "CoreV1Api", return_value=v1),
            patch.object(agent.nats, "connect", new=AsyncMock(return_value=nc)),
            patch.object(agent, "nats_url", return_value="nats://unused:4222"),
            patch.object(agent.ops_events, "init", new=AsyncMock()),
            patch.object(agent.WiringManifest, "model_validate", return_value=manifest),
            patch.object(agent, "expected_local_nodes", return_value=set()),
            patch.object(substrate_monitor, "configure_required_measurements"),
            patch.object(substrate_monitor, "monitor_loop", new=monitor),
        ):
            asyncio.run(run())
        print("agent process stopped")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script, shutdown],
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
        capture_output=True,
        text=True,
        timeout=8,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "agent process stopped" in result.stdout
