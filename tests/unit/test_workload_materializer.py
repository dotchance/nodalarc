# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The shared Pod assembly contract, exercised directly with sentinels."""

from __future__ import annotations

import kubernetes.client
import pytest
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
        "tolerations": [],
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
    assert pod.spec.node_name is None
    terms = pod.spec.affinity.node_affinity.required_during_scheduling_ignored_during_execution
    assert [
        (field.key, field.operator, field.values)
        for term in terms.node_selector_terms
        for field in term.match_fields
    ] == [("metadata.name", "In", ["node02"])]
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


# ---------------------------------------------------------------------------
# The release gate script, executed
# ---------------------------------------------------------------------------


def _run_gate(tmp_path, *, pod_uid="uid-1", run="run-1", status_file=None):
    """Start the gate with its status file redirected into tmp_path."""
    import os
    import subprocess

    from nodalarc_operator.workloads.materializer import _WIRING_GATE_SCRIPT

    status_file = status_file or tmp_path / "status.json"
    script = _WIRING_GATE_SCRIPT.replace("/wiring-status/status.json", str(status_file))
    process = subprocess.Popen(
        ["bash", "-c", script],
        env={**os.environ, "NODE_ID": "sat-a", "POD_UID": pod_uid, "SESSION_RUN_ID": run},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        # Its own process group, so cleanup reaches the backgrounded loop too.
        # In a container the loop ends with the PID namespace; here it would not.
        start_new_session=True,
    )
    return process, status_file


def _kill_gate(process) -> None:
    import contextlib
    import os
    import signal

    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=5)


def _gate_row(*, pod_uid, run, netns):
    from nodalarc.substrate.manifest_contract import REQUIRED_WIRING_PHASES

    return {
        "node_id": "sat-a",
        "session_id": run,
        "session_run_id": run,
        "wiring_generation": "sha256:" + "a" * 64,
        "pod_uid": pod_uid,
        "sandbox_id": "sb",
        "netns_id": netns,
        "status": "ready",
        "phases": [{"phase": phase, "status": "ready"} for phase in REQUIRED_WIRING_PHASES],
        "dirty_kernel": False,
    }


def _own_netns() -> str:
    import os

    return os.readlink("/proc/self/ns/net").removeprefix("net:[").removesuffix("]")


def test_gate_releases_within_a_second_of_matching_proof(tmp_path) -> None:
    import json
    import time

    process, status_file = _run_gate(tmp_path)
    try:
        # Proof for another pod incarnation never releases the gate.
        status_file.write_text(
            json.dumps(_gate_row(pod_uid="uid-other", run="run-1", netns=_own_netns()))
        )
        time.sleep(0.6)
        assert process.poll() is None
        started = time.monotonic()
        status_file.write_text(
            json.dumps(_gate_row(pod_uid="uid-1", run="run-1", netns=_own_netns()))
        )
        assert process.wait(timeout=5) == 0
        assert time.monotonic() - started < 1.0
    finally:
        _kill_gate(process)
    assert "wiring ready for sat-a" in process.stdout.read()


def test_gate_releases_on_the_proof_the_node_agent_delivers(monkeypatch, tmp_path) -> None:
    """The Node Agent's delivery path and the gate's reading path are one file."""
    import json
    import time

    from nodalarc.substrate.wiring_status import WIRING_STATUS_FILE, wiring_status_host_path
    from node_agent.proof_delivery import deliver_proof_file

    volume = tmp_path / wiring_status_host_path(".", "uid-1")
    volume.mkdir(parents=True)
    process, _ = _run_gate(tmp_path, status_file=volume / WIRING_STATUS_FILE)
    try:
        time.sleep(0.3)
        assert process.poll() is None
        started = time.monotonic()
        deliver_proof_file(
            str(tmp_path),
            "uid-1",
            json.dumps(_gate_row(pod_uid="uid-1", run="run-1", netns=_own_netns())),
        )
        assert process.wait(timeout=5) == 0
        assert time.monotonic() - started < 1.0
    finally:
        _kill_gate(process)


def test_gate_exits_on_sigterm_without_reading_as_ready(tmp_path) -> None:
    import signal
    import time

    process, _status_file = _run_gate(tmp_path)
    try:
        time.sleep(0.3)
        started = time.monotonic()
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=5) == 143
        assert time.monotonic() - started < 1.0
    finally:
        _kill_gate(process)


@pytest.mark.parametrize(
    ("image", "configured", "expected"),
    [
        # A digest names immutable content: a node that holds it need not ask again.
        ("reg:5000/nodalarc/frr:abc@sha256:" + "a" * 64, "Always", "IfNotPresent"),
        ("registry.example/nodalarc/frr@sha256:" + "a" * 64, "Always", "IfNotPresent"),
        # A tag can move: the configured policy stands.
        ("reg:5000/nodalarc/frr:abc", "Always", "Always"),
        # Every other configured policy is kept as it is.
        ("reg:5000/nodalarc/frr:abc@sha256:" + "a" * 64, "Never", "Never"),
        ("reg:5000/nodalarc/frr:abc", "IfNotPresent", "IfNotPresent"),
    ],
)
def test_pull_policy_follows_the_reference_form(image, configured, expected) -> None:
    from nodalarc_operator.workloads.materializer import image_pull_policy_for

    assert image_pull_policy_for(image, configured) == expected
