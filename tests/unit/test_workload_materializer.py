# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The shared Pod assembly contract, exercised directly with sentinels."""

from __future__ import annotations

import kubernetes.client
import pytest
from nodalarc_operator.workloads.materializer import (
    WorkloadComposition,
    build_session_pod,
)

OWNER_REF = {"kind": "ConstellationSpec", "name": "s", "uid": "owner-uid-1"}


@pytest.fixture(autouse=True)
def _gate_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WIRING_GATE_IMAGE", "test/base:1")
    monkeypatch.setenv("IMAGE_PULL_POLICY", "Never")


def _container(name: str) -> kubernetes.client.V1Container:
    return kubernetes.client.V1Container(name=name, image=f"img/{name}@sha256:{'a' * 64}")


def _volume(name: str) -> kubernetes.client.V1Volume:
    return kubernetes.client.V1Volume(
        name=name, empty_dir=kubernetes.client.V1EmptyDirVolumeSource()
    )


def _build(composition: WorkloadComposition, **overrides) -> kubernetes.client.V1Pod:
    kwargs = {
        "pod_name": "sat-x",
        "namespace": "nodalarc",
        "node_id": "sat-X",
        "role": "satellite",
        "session_id": "run-test-0001",
        "owner_ref": OWNER_REF,
        "composition": composition,
        "selection_identity": "builtin-frr-default",
        "target_node": "node02",
    }
    kwargs.update(overrides)
    return build_session_pod(**kwargs)


def test_assembly_contract_with_sentinel_composition() -> None:
    composition = WorkloadComposition(
        containers=[_container("frr"), _container("observer")],
        volumes=[_volume("vol-a"), _volume("vol-b")],
        init_containers=[_container("authored-init")],
    )
    pod = _build(composition)

    # Gate-first ordering; authored init containers preserved after it.
    init_names = [c.name for c in pod.spec.init_containers]
    assert init_names == ["wiring-gate", "authored-init"]

    # Authored composition preserved verbatim and in order.
    assert [c.name for c in pod.spec.containers] == ["frr", "observer"]
    volume_names = [v.name for v in pod.spec.volumes]
    assert volume_names[:2] == ["vol-a", "vol-b"]
    assert volume_names[-1] == "wiring-status"

    # Node placement, restart policy, token policy, DNS policy.
    assert pod.spec.node_name == "node02"
    assert pod.spec.restart_policy == "Never"
    assert pod.spec.automount_service_account_token is False
    dns = {option.name: option.value for option in pod.spec.dns_config.options}
    assert dns == {"timeout": "1", "attempts": "1"}

    # Platform identity labels.
    labels = pod.metadata.labels
    assert labels["nodalarc.io/node-id"] == "sat-X"
    assert labels["nodalarc.io/session-run-id"] == "run-test-0001"
    assert labels["nodalarc.io/owner-uid"] == "owner-uid-1"
    assert pod.metadata.owner_references == [OWNER_REF]


def test_extra_labels_may_not_override_identity() -> None:
    composition = WorkloadComposition(containers=[_container("frr")], volumes=[])
    with pytest.raises(ValueError, match="platform identity labels"):
        _build(composition, extra_labels={"nodalarc.io/node-id": "spoofed"})


def test_reserved_names_are_rejected() -> None:
    with pytest.raises(ValueError, match="wiring-gate"):
        _build(WorkloadComposition(containers=[_container("wiring-gate")], volumes=[]))
    with pytest.raises(ValueError, match="wiring-status"):
        _build(
            WorkloadComposition(containers=[_container("frr")], volumes=[_volume("wiring-status")])
        )
