"""PodLocationMap public API contracts."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import kubernetes.client
import kubernetes.config
import pytest
from scheduler.pod_locator import PodLocationMap


def test_pod_location_loader_does_not_expose_legacy_agent_port() -> None:
    params = inspect.signature(PodLocationMap.load_from_k8s_api).parameters
    assert "agent_port" not in params
    assert "_agent_port" not in params


def _pod(node_id: str, session_id: str, k3s_node: str):
    return SimpleNamespace(
        metadata=SimpleNamespace(
            labels={
                "nodalarc.io/node-id": node_id,
                "nodalarc.io/session-run-id": session_id,
            }
        ),
        spec=SimpleNamespace(node_name=k3s_node),
    )


def _node(name: str, ip: str):
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name),
        status=SimpleNamespace(addresses=[SimpleNamespace(type="InternalIP", address=ip)]),
    )


def test_k8s_loader_filters_to_active_resolved_session(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeV1:
        def list_namespaced_pod(self, namespace, label_selector):
            assert namespace == "nodalarc"
            assert label_selector == "nodalarc.io/node-id"
            return SimpleNamespace(
                items=[
                    _pod("earth-leo-sat-p00s00", "run-current", "node01"),
                    _pod("earth-leo-sat-p00s01", "run-current", "node02"),
                    _pod("stale-sat-p00s00", "run-old", "node03"),
                    _pod("earth-leo-sat-p00s02", "run-old", "node03"),
                ]
            )

        def list_node(self):
            return SimpleNamespace(
                items=[
                    _node("node01", "192.0.2.1"),
                    _node("node02", "192.0.2.2"),
                    _node("node03", "192.0.2.3"),
                ]
            )

    monkeypatch.setattr(kubernetes.config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(kubernetes.client, "CoreV1Api", FakeV1)

    loc = PodLocationMap()
    loc.load_from_k8s_api(
        namespace="nodalarc",
        expected_node_ids={"earth-leo-sat-p00s00", "earth-leo-sat-p00s01"},
        session_id="run-current",
    )

    assert sorted(loc.node_ids) == ["earth-leo-sat-p00s00", "earth-leo-sat-p00s01"]
    assert loc.k3s_node("earth-leo-sat-p00s00") == "node01"
    assert loc.k3s_node("earth-leo-sat-p00s01") == "node02"
    assert "node03" not in loc.all_agent_addrs()


def test_k8s_loader_fails_when_active_expected_node_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeV1:
        def list_namespaced_pod(self, namespace, label_selector):
            return SimpleNamespace(items=[_pod("earth-leo-sat-p00s00", "run-current", "node01")])

        def list_node(self):
            return SimpleNamespace(items=[_node("node01", "192.0.2.1")])

    monkeypatch.setattr(kubernetes.config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(kubernetes.client, "CoreV1Api", FakeV1)

    loc = PodLocationMap()
    with pytest.raises(RuntimeError, match="Missing active session pod"):
        loc.load_from_k8s_api(
            namespace="nodalarc",
            expected_node_ids={"earth-leo-sat-p00s00", "earth-leo-sat-p00s01"},
            session_id="run-current",
        )


def test_k8s_loader_does_not_treat_pending_pod_as_located(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeV1:
        def list_namespaced_pod(self, namespace, label_selector):
            return SimpleNamespace(items=[_pod("earth-leo-sat-p00s00", "run-current", "")])

        def list_node(self):
            return SimpleNamespace(items=[])

    monkeypatch.setattr(kubernetes.config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(kubernetes.client, "CoreV1Api", FakeV1)

    loc = PodLocationMap()
    with pytest.raises(RuntimeError, match="Missing active session pod"):
        loc.load_from_k8s_api(
            namespace="nodalarc",
            expected_node_ids={"earth-leo-sat-p00s00"},
            session_id="run-current",
        )


def test_an_unknown_node_or_node_ip_is_refused() -> None:
    from scheduler.pod_locator import PodLocationError

    loc = PodLocationMap()
    loc._node_of["sat-a"] = "node01"
    loc._agent_addrs["node01"] = "node01"
    loc._node_ips["node01"] = "192.168.10.201"

    assert (loc.k3s_node("sat-a"), loc.agent_addr("sat-a"), loc.node_ip("node01")) == (
        "node01",
        "node01",
        "192.168.10.201",
    )
    with pytest.raises(PodLocationError, match="no pod location for node sat-ghost"):
        loc.k3s_node("sat-ghost")
    with pytest.raises(PodLocationError, match="no pod location for node sat-ghost"):
        loc.agent_addr("sat-ghost")
    with pytest.raises(PodLocationError, match="no pod location for node sat-ghost"):
        loc.link_locality("sat-a", "sat-ghost")
    with pytest.raises(PodLocationError, match="no InternalIP for Kubernetes node node09"):
        loc.node_ip("node09")


def test_a_hosting_node_without_an_internal_ip_is_refused_at_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scheduler.pod_locator import PodLocationError

    class _Api:
        def list_namespaced_pod(self, namespace, *, label_selector):
            return SimpleNamespace(items=[_pod("earth-leo-sat-p00s00", "run-current", "node01")])

        def list_node(self):
            return SimpleNamespace(
                items=[
                    SimpleNamespace(
                        metadata=SimpleNamespace(name="node01"),
                        status=SimpleNamespace(addresses=[]),
                    )
                ]
            )

    monkeypatch.setattr(kubernetes.config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(kubernetes.client, "CoreV1Api", lambda: _Api())

    with pytest.raises(PodLocationError, match="have no InternalIP: node01"):
        PodLocationMap().load_from_k8s_api(
            namespace="nodalarc",
            expected_node_ids={"earth-leo-sat-p00s00"},
            session_id="run-current",
        )
