"""The measurement adapter reaches the container the Operator published."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from measurement.adapters import base_frr_adapter as adapter_module
from nodalarc.workload_target import WorkloadTargetError

from tests.unit.test_workload_target import pod_document


def _state(node_id: str = "leo-sat-p00s00") -> adapter_module._NodeState:
    state = adapter_module._NodeState(node_id, "10.42.0.7")
    state._pod_name = node_id
    state._namespace = "nodalarc"
    return state


def _kubectl_returning(stdout: str, returncode: int = 0):
    calls: list[list[str]] = []

    def run(argv, **_kwargs):
        calls.append(list(argv))
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="denied")

    return run, calls


def test_container_is_the_published_primary_not_the_first_listed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, calls = _kubectl_returning(json.dumps(pod_document()))
    monkeypatch.setattr(adapter_module.subprocess, "run", run)

    state = _state()

    assert state.container == "custom-router"
    assert state.container == "custom-router"
    assert len(calls) == 1
    assert calls[0][:6] == ["kubectl", "get", "pod", "-n", "nodalarc", "leo-sat-p00s00"]


def test_missing_target_metadata_is_a_typed_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    run, _calls = _kubectl_returning(json.dumps(pod_document(primary=None)))
    monkeypatch.setattr(adapter_module.subprocess, "run", run)

    with pytest.raises(WorkloadTargetError, match="carries no nodalarc.io/primary-container"):
        _ = _state().container


def test_kubectl_failure_is_a_typed_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    run, _calls = _kubectl_returning("", returncode=1)
    monkeypatch.setattr(adapter_module.subprocess, "run", run)

    with pytest.raises(WorkloadTargetError, match="kubectl get pod leo-sat-p00s00 failed: denied"):
        _ = _state().container
