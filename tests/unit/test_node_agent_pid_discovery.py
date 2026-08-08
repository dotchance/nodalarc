# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Validated sandbox discovery: run fencing, identity checks, fail-closed."""

from __future__ import annotations

import json
import os
import subprocess
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from node_agent import pid_discovery
from node_agent.pid_discovery import NamespaceHandle, netns_identity, verify_handle

RUN_ID = "run-test-0001"
OWNER_UID = "owner-uid-1"
LIVE_PID = os.getpid()


POD_IP = "10.42.0.7"
HOST_NETNS = "4026531840"
TEST_NETNS = "4026533700"


def _pod(node_id: str | None, uid: str) -> SimpleNamespace:
    labels = {"nodalarc.io/role": "satellite"}
    if node_id is not None:
        labels["nodalarc.io/node-id"] = node_id
    return SimpleNamespace(
        metadata=SimpleNamespace(labels=labels, uid=uid),
        status=SimpleNamespace(container_statuses=None, pod_ip=POD_IP),
    )


def _fake_netns(pid: int) -> str | None:
    if pid == 1:
        return HOST_NETNS
    if pid == LIVE_PID:
        return TEST_NETNS
    return None


def _sandbox(
    uid: str, sandbox_id: str, *, state: str = "SANDBOX_READY", attempt: int = 0
) -> dict[str, Any]:
    return {
        "id": sandbox_id,
        "metadata": {"uid": uid, "name": "x", "attempt": attempt},
        "state": state,
    }


class _FakeCrictl:
    """Dispatch fake crictl calls and record every invocation."""

    def __init__(
        self,
        sandboxes: list[dict[str, Any]],
        inspect: dict[str, dict[str, Any]],
        *,
        fail_listing: bool = False,
    ) -> None:
        self.sandboxes = sandboxes
        self.inspect = inspect
        self.fail_listing = fail_listing
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
        self.calls.append(list(cmd))
        args = [part for part in cmd if part != "crictl"]
        if "--runtime-endpoint" in args:
            index = args.index("--runtime-endpoint")
            del args[index : index + 2]
        if args[:1] == ["pods"]:
            if self.fail_listing:
                raise subprocess.CalledProcessError(1, cmd)
            return SimpleNamespace(stdout=json.dumps({"items": self.sandboxes}))
        if args[:1] == ["inspectp"]:
            payload = self.inspect.get(args[1])
            if payload is None:
                raise subprocess.CalledProcessError(1, cmd)
            status = payload.get("status", {})
            if status.get("id") is None:
                status["id"] = args[1]
            metadata = status.get("metadata", {})
            if metadata.get("attempt") is None:
                requested = next(
                    (s for s in self.sandboxes if s["id"] == args[1]), {"metadata": {}}
                )
                metadata["attempt"] = requested["metadata"].get("attempt", 0)
            return SimpleNamespace(stdout=json.dumps(payload))
        raise AssertionError(f"unexpected crictl invocation: {cmd}")


def _inspectp(
    uid: str,
    pid: int,
    *,
    state: str = "SANDBOX_READY",
    sandbox_id: str | None = None,
    attempt: int | None = None,
    ip: str | None = POD_IP,
) -> dict[str, Any]:
    return {
        "status": {
            "id": sandbox_id,
            "metadata": {"uid": uid, "attempt": attempt},
            "state": state,
            "network": {"ip": ip},
        },
        "info": {"pid": pid},
    }


class _Recorder:
    def __init__(self, pods: list[SimpleNamespace]) -> None:
        self.listing = SimpleNamespace(items=pods)
        self.kwargs: dict[str, Any] = {}

    def list_namespaced_pod(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        self.kwargs = kwargs
        return self.listing


def _discover(
    pods: list[SimpleNamespace], crictl: _FakeCrictl, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, NamespaceHandle], _Recorder]:
    monkeypatch.delenv("CONTAINER_RUNTIME_ENDPOINT", raising=False)
    monkeypatch.setattr(pid_discovery, "netns_identity", _fake_netns)
    api = _Recorder(pods)
    with (
        patch("kubernetes.config.load_incluster_config"),
        patch("kubernetes.client.CoreV1Api", return_value=api),
        patch.object(pid_discovery.subprocess, "run", crictl),
    ):
        result = pid_discovery.discover_local_pod_handles(
            namespace="testns",
            node_name="node02",
            session_run_id=RUN_ID,
            owner_uid=OWNER_UID,
        )
    return result, api


def test_discovery_is_fenced_to_the_active_run(monkeypatch: pytest.MonkeyPatch) -> None:
    crictl = _FakeCrictl([_sandbox("uid-1", "sb-1")], {"sb-1": _inspectp("uid-1", LIVE_PID)})
    result, api = _discover([_pod("sat-0-0", "uid-1")], crictl, monkeypatch)
    assert set(result) == {"sat-0-0"}
    selector = api.kwargs["label_selector"]
    assert f"nodalarc.io/session-run-id={RUN_ID}" in selector
    assert f"nodalarc.io/owner-uid={OWNER_UID}" in selector


def test_handle_carries_validated_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    crictl = _FakeCrictl(
        [_sandbox("uid-1", "sb-1", attempt=2)], {"sb-1": _inspectp("uid-1", LIVE_PID)}
    )
    result, _ = _discover([_pod("sat-0-0", "uid-1")], crictl, monkeypatch)
    handle = result["sat-0-0"]
    assert handle.pod_uid == "uid-1"
    assert handle.sandbox_id == "sb-1"
    assert handle.sandbox_attempt == 2
    assert handle.pid == LIVE_PID
    assert handle.netns_id == TEST_NETNS


def test_pod_with_no_containers_is_discovered(monkeypatch: pytest.MonkeyPatch) -> None:
    crictl = _FakeCrictl([_sandbox("uid-1", "sb-1")], {"sb-1": _inspectp("uid-1", LIVE_PID)})
    result, _ = _discover([_pod("sat-0-0", "uid-1")], crictl, monkeypatch)
    assert set(result) == {"sat-0-0"}
    subcommands = {
        call[call.index("crictl") + 1] if "crictl" in call else call[0] for call in crictl.calls
    }
    assert "inspect" not in subcommands
    assert subcommands <= {"pods", "inspectp"}


def test_duplicate_node_ids_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    crictl = _FakeCrictl(
        [_sandbox("uid-1", "sb-1"), _sandbox("uid-2", "sb-2")],
        {"sb-1": _inspectp("uid-1", LIVE_PID), "sb-2": _inspectp("uid-2", LIVE_PID)},
    )
    pods = [_pod("sat-0-0", "uid-1"), _pod("sat-0-0", "uid-2")]
    result, _ = _discover(pods, crictl, monkeypatch)
    assert result == {}


def test_highest_ready_attempt_wins_deterministically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crictl = _FakeCrictl(
        [
            _sandbox("uid-1", "sb-old", attempt=0),
            _sandbox("uid-1", "sb-new", attempt=1),
        ],
        {"sb-new": _inspectp("uid-1", LIVE_PID)},
    )
    result, _ = _discover([_pod("sat-0-0", "uid-1")], crictl, monkeypatch)
    assert result["sat-0-0"].sandbox_id == "sb-new"


def test_equal_ready_attempts_are_ambiguous_and_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crictl = _FakeCrictl(
        [
            _sandbox("uid-1", "sb-a", attempt=1),
            _sandbox("uid-1", "sb-b", attempt=1),
        ],
        {},
    )
    result, _ = _discover([_pod("sat-0-0", "uid-1")], crictl, monkeypatch)
    assert result == {}


def test_inspect_identity_mismatch_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    crictl = _FakeCrictl([_sandbox("uid-1", "sb-1")], {"sb-1": _inspectp("other-uid", LIVE_PID)})
    result, _ = _discover([_pod("sat-0-0", "uid-1")], crictl, monkeypatch)
    assert result == {}


def test_sandbox_no_longer_ready_at_inspect_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crictl = _FakeCrictl(
        [_sandbox("uid-1", "sb-1")],
        {"sb-1": _inspectp("uid-1", LIVE_PID, state="SANDBOX_NOTREADY")},
    )
    result, _ = _discover([_pod("sat-0-0", "uid-1")], crictl, monkeypatch)
    assert result == {}


def test_dead_pid_has_no_netns_and_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    crictl = _FakeCrictl([_sandbox("uid-1", "sb-1")], {"sb-1": _inspectp("uid-1", 2**22 + 12345)})
    result, _ = _discover([_pod("sat-0-0", "uid-1")], crictl, monkeypatch)
    assert result == {}


def test_notready_sandbox_is_ignored_in_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    crictl = _FakeCrictl([_sandbox("uid-1", "sb-1", state="SANDBOX_NOTREADY")], {})
    result, _ = _discover([_pod("sat-0-0", "uid-1")], crictl, monkeypatch)
    assert result == {}


def test_pod_without_sandbox_is_omitted_not_invented(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crictl = _FakeCrictl([_sandbox("uid-1", "sb-1")], {"sb-1": _inspectp("uid-1", LIVE_PID)})
    pods = [_pod("sat-0-0", "uid-1"), _pod("sat-0-1", "uid-2")]
    result, _ = _discover(pods, crictl, monkeypatch)
    assert set(result) == {"sat-0-0"}


def test_listing_failure_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    crictl = _FakeCrictl([], {}, fail_listing=True)
    result, _ = _discover([_pod("sat-0-0", "uid-1")], crictl, monkeypatch)
    assert result == {}


def test_missing_run_identity_is_refused() -> None:
    with pytest.raises(ValueError, match="session_run_id"):
        pid_discovery.discover_local_pod_handles(
            namespace="testns", node_name="node02", session_run_id="", owner_uid=OWNER_UID
        )


def test_verify_handle_detects_namespace_replacement() -> None:
    live = NamespaceHandle(
        node_id="sat-0-0",
        pod_uid="uid-1",
        sandbox_id="sb-1",
        sandbox_attempt=0,
        pid=LIVE_PID,
        netns_id=netns_identity(LIVE_PID) or "0",
    )
    assert verify_handle(live)
    replaced = NamespaceHandle(
        node_id="sat-0-0",
        pod_uid="uid-1",
        sandbox_id="sb-1",
        sandbox_attempt=0,
        pid=LIVE_PID,
        netns_id="1",
    )
    assert not verify_handle(replaced)


def test_host_netns_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A PID resolving to the host network namespace must never be trusted."""
    crictl = _FakeCrictl([_sandbox("uid-1", "sb-1")], {"sb-1": _inspectp("uid-1", 1)})
    result, _ = _discover([_pod("sat-0-0", "uid-1")], crictl, monkeypatch)
    assert result == {}


def test_sandbox_ip_mismatch_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    crictl = _FakeCrictl(
        [_sandbox("uid-1", "sb-1")],
        {"sb-1": _inspectp("uid-1", LIVE_PID, ip="10.99.99.99")},
    )
    result, _ = _discover([_pod("sat-0-0", "uid-1")], crictl, monkeypatch)
    assert result == {}


def test_sandbox_id_echo_mismatch_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    crictl = _FakeCrictl(
        [_sandbox("uid-1", "sb-1")],
        {"sb-1": _inspectp("uid-1", LIVE_PID, sandbox_id="sb-other")},
    )
    result, _ = _discover([_pod("sat-0-0", "uid-1")], crictl, monkeypatch)
    assert result == {}
