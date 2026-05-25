import pytest
from node_agent import ops_events
from node_agent.__main__ import (
    _explicit_fence_from_env,
    _require_host_ip_for_vxlan_capable_startup,
    _require_ready_fence,
)
from node_agent.command_contract import RuntimeFence
from node_agent.mpls import load_mpls_kernel_modules


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


def test_loads_mpls_kernel_modules_in_k8s(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setenv("NODE_NAME", "k3s-a")

    class Result:
        returncode = 0
        stderr = ""

    def run(cmd, **_kwargs):
        calls.append(cmd)
        return Result()

    monkeypatch.setattr("node_agent.mpls.subprocess.run", run)

    load_mpls_kernel_modules()

    assert calls == [["modprobe", "mpls_router"], ["modprobe", "mpls_iptunnel"]]


def test_reports_unavailable_mpls_kernel_module(monkeypatch: pytest.MonkeyPatch) -> None:
    published: list[dict] = []
    monkeypatch.setenv("NODE_NAME", "k3s-a")

    class Result:
        returncode = 1
        stderr = "module not found"

    monkeypatch.setattr("node_agent.mpls.subprocess.run", lambda *_args, **_kwargs: Result())
    monkeypatch.setattr(ops_events, "publish", lambda **kwargs: published.append(kwargs))

    load_mpls_kernel_modules()

    assert published
    assert published[0]["code"] == "STARTUP_KERNEL_MODULE_UNAVAILABLE"
    assert published[0]["details"]["module"] == "mpls_router"


def test_explicit_pid_map_fence_requires_both_identity_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NODE_AGENT_SESSION_ID", "demo")
    monkeypatch.delenv("NODE_AGENT_WIRING_GENERATION", raising=False)

    with pytest.raises(RuntimeError, match="must be provided together"):
        _explicit_fence_from_env()


def test_explicit_pid_map_fence_sanitizes_session_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NODE_AGENT_SESSION_ID", "demo.session")
    monkeypatch.setenv("NODE_AGENT_WIRING_GENERATION", "sha256:" + "a" * 64)

    fence = _explicit_fence_from_env()

    assert fence == RuntimeFence(session_id="demo-session", wiring_generation="sha256:" + "a" * 64)


def test_ready_fence_missing_identity_fails_before_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[dict] = []
    monkeypatch.setattr(ops_events, "publish", lambda **kwargs: published.append(kwargs))

    with pytest.raises(RuntimeError, match="wiring identity unavailable"):
        _require_ready_fence(RuntimeFence(session_id="", wiring_generation=""))

    assert published[0]["code"] == "STARTUP_WIRING_IDENTITY_MISSING"
