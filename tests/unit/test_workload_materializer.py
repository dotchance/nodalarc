# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The shared Pod assembly contract, exercised directly with sentinels."""

from __future__ import annotations

import kubernetes.client
import pytest
from nodalarc.substrate.wiring_status import READY_PHASE_JQ_CLAUSE
from nodalarc.workload_target import workload_target_from_pod
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


def _single(name: str, *, volumes: list | None = None) -> WorkloadComposition:
    return WorkloadComposition(
        containers=[_container(name)], volumes=volumes or [], primary_container=name
    )


def _metadata_json(pod: kubernetes.client.V1Pod) -> dict:
    return kubernetes.client.ApiClient().sanitize_for_serialization(pod)["metadata"]


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
        primary_container="frr",
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

    # The primary workload target is published on the pod and reads back
    # through the shared reader, whatever the container order.
    assert pod.metadata.annotations["nodalarc.io/primary-container"] == "frr"
    target = workload_target_from_pod(
        kubernetes.client.ApiClient().sanitize_for_serialization(pod)
        | {"metadata": {**_metadata_json(pod), "uid": "uid-1"}}
    )
    assert target.container == "frr"
    assert target.node_id == "sat-X"


def test_extra_labels_may_not_override_identity() -> None:
    composition = _single("frr")
    with pytest.raises(ValueError, match="platform identity labels"):
        _build(composition, extra_labels={"nodalarc.io/node-id": "spoofed"})


def test_reserved_names_are_rejected() -> None:
    with pytest.raises(ValueError, match="wiring-gate"):
        _build(_single("wiring-gate"))
    with pytest.raises(ValueError, match="wiring-status"):
        _build(_single("frr", volumes=[_volume("wiring-status")]))


def test_renamed_primary_with_sidecar_first_is_published_by_name() -> None:
    composition = WorkloadComposition(
        containers=[_container("observer"), _container("custom-router")],
        volumes=[],
        primary_container="custom-router",
    )
    pod = _build(composition)

    assert pod.metadata.annotations["nodalarc.io/primary-container"] == "custom-router"


def test_primary_container_must_be_one_of_the_declared_containers() -> None:
    composition = WorkloadComposition(
        containers=[_container("frr-router"), _container("observer")],
        volumes=[],
        primary_container="frr",
    )
    with pytest.raises(ValueError, match="names primary container 'frr' but declares"):
        _build(composition)


def test_release_gate_phase_clause_is_the_shared_rule() -> None:
    composition = _single("frr")
    pod = _build(composition)
    gate = next(
        container for container in pod.spec.init_containers if container.name == "wiring-gate"
    )
    script = " ".join(gate.args or gate.command or [])

    assert READY_PHASE_JQ_CLAUSE in script
    assert "length > 0" not in script
