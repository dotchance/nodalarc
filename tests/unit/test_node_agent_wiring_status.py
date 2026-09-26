import pytest
from nodalarc.substrate.manifest_contract import REQUIRED_WIRING_PHASES, WiringManifest
from nodalarc.substrate.wiring_status import failed_status


def _manifest() -> WiringManifest:
    return WiringManifest.model_validate(
        {
            "session_id": "demo",
            "session_run_id": "run-demo-0001",
            "owner_uid": "owner-uid-1",
            "wiring_generation": "sha256:" + "a" * 64,
            "required_phases": list(REQUIRED_WIRING_PHASES),
            "nodes": {
                "sat-a": {
                    "node_type": "satellite",
                    "host": "node02",
                    "plane": 0,
                    "slot": 0,
                    "sysctls": {"net.ipv6.conf.all.forwarding": "1"},
                    "isl_interfaces": [],
                    "gnd_interfaces": [],
                    "mpls_enable": True,
                    "remove_default_route": True,
                }
            },
            "ground_bridges": {},
            "site_lans": {},
            "required_substrate_pairs": [],
            "isl_link_count": 0,
        }
    )


def test_failed_status_marks_prior_ready_failed_phase_dirty_and_later_pending() -> None:
    status = failed_status(
        "sat-a",
        _manifest(),
        pod_uid="pod-uid-1",
        sandbox_id="sb-1",
        netns_id="4026532100",
        phase="ground_infrastructure",
        error_message="bridge failed",
        dirty_kernel=True,
    )

    phases = {phase.phase: phase for phase in status.phases}
    assert phases["managed_interface_cleanup"].status == "ready"
    assert phases["sysctls"].status == "ready"
    assert phases["ground_infrastructure"].status == "dirty_kernel"
    assert phases["ground_infrastructure"].error_message == "bridge failed"
    assert phases["terrestrial_interfaces"].status == "pending_pid"
    assert phases["pod_route_finalization"].status == "pending_pid"
    assert phases["pod_security"].status == "pending_pid"


def test_failed_status_rejects_unknown_phase() -> None:
    with pytest.raises(ValueError, match="unknown wiring failure phase"):
        failed_status(
            "sat-a",
            _manifest(),
            pod_uid="pod-uid-1",
            sandbox_id="sb-1",
            netns_id="4026532100",
            phase="not_a_phase",
            error_message="bad phase",
        )


# ---------------------------------------------------------------------------
# The proof lives on the pod it proves
# ---------------------------------------------------------------------------


def _handle(node_id: str, *, pod_uid: str):
    from node_agent.pid_discovery import NamespaceHandle

    return NamespaceHandle(
        node_id=node_id,
        pod_name=node_id,
        pod_uid=pod_uid,
        sandbox_id=f"sb-{node_id}",
        sandbox_attempt=0,
        pid=4242,
        netns_id="4026532100",
        mpls_enable=False,
    )


def _ready(node_id: str, pod_uid: str):
    from nodalarc.substrate.wiring_status import wiring_row

    return wiring_row(
        node_id,
        _manifest(),
        pod_uid=pod_uid,
        sandbox_id=f"sb-{node_id}",
        netns_id="4026532100",
        state="ready",
    )


def _kubelet_pods(monkeypatch, tmp_path, *pod_uids):
    """A kubelet pods directory holding each pod's wiring-status volume."""
    from nodalarc.substrate.wiring_status import wiring_status_host_path

    for pod_uid in pod_uids:
        (tmp_path / wiring_status_host_path(".", pod_uid)).mkdir(parents=True)
    monkeypatch.setenv("KUBELET_PODS_DIR", str(tmp_path))
    return tmp_path


def test_write_wiring_status_patches_each_pod_under_its_uid(monkeypatch, tmp_path) -> None:
    from unittest.mock import MagicMock

    from nodalarc.substrate.wiring_status import WIRING_STATUS_ANNOTATION, decode_status
    from node_agent import wiring
    from node_agent.proof_delivery import delivered_proof

    pods_dir = _kubelet_pods(monkeypatch, tmp_path, "uid-a", "uid-b")
    v1 = MagicMock()
    monkeypatch.setattr(wiring.kubernetes.config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(wiring.kubernetes.client, "CoreV1Api", lambda: v1)
    statuses = {"sat-a": _ready("sat-a", "uid-a"), "sat-b": _ready("sat-b", "uid-b")}
    handles = {
        "sat-a": _handle("sat-a", pod_uid="uid-a"),
        "sat-b": _handle("sat-b", pod_uid="uid-b"),
    }

    wiring.write_wiring_status(statuses, handles, namespace="nodalarc")

    patched = {call.args[0]: call.args[2] for call in v1.patch_namespaced_pod.call_args_list}
    assert set(patched) == {"sat-a", "sat-b"}
    for node_id, body in patched.items():
        # The UID in the body makes the API server refuse a replaced pod.
        assert body["metadata"]["uid"] == statuses[node_id].pod_uid
        annotation = body["metadata"]["annotations"][WIRING_STATUS_ANNOTATION]
        assert decode_status(annotation) == statuses[node_id]
        # The pod's gate receives exactly the bytes of the annotation.
        assert delivered_proof(str(pods_dir), statuses[node_id].pod_uid) == annotation


def test_write_wiring_status_refuses_a_pod_without_its_volume(monkeypatch, tmp_path) -> None:
    """A pod whose wiring-status volume is not on this host is a failed write."""
    from unittest.mock import MagicMock

    from node_agent import wiring

    _kubelet_pods(monkeypatch, tmp_path, "uid-a")
    v1 = MagicMock()
    monkeypatch.setattr(wiring.kubernetes.config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(wiring.kubernetes.client, "CoreV1Api", lambda: v1)
    statuses = {"sat-a": _ready("sat-a", "uid-a"), "sat-b": _ready("sat-b", "uid-b")}
    handles = {
        "sat-a": _handle("sat-a", pod_uid="uid-a"),
        "sat-b": _handle("sat-b", pod_uid="uid-b"),
    }

    with pytest.raises(RuntimeError, match=r"failed for 1 pod\(s\): sat-b: .*proof file"):
        wiring.write_wiring_status(statuses, handles, namespace="nodalarc")


def test_write_wiring_status_requires_the_kubelet_pods_dir(monkeypatch) -> None:
    from unittest.mock import MagicMock

    from node_agent import wiring

    monkeypatch.delenv("KUBELET_PODS_DIR", raising=False)
    v1 = MagicMock()
    monkeypatch.setattr(wiring.kubernetes.config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(wiring.kubernetes.client, "CoreV1Api", lambda: v1)
    with pytest.raises(RuntimeError, match="KUBELET_PODS_DIR env var is required"):
        wiring.write_wiring_status(
            {"sat-a": _ready("sat-a", "uid-a")},
            {"sat-a": _handle("sat-a", pod_uid="uid-a")},
            namespace="nodalarc",
        )
    v1.patch_namespaced_pod.assert_not_called()


def test_write_wiring_status_raises_naming_every_failed_pod(monkeypatch, tmp_path) -> None:
    from unittest.mock import MagicMock

    import kubernetes
    from node_agent import wiring

    _kubelet_pods(monkeypatch, tmp_path, "uid-a", "uid-b")
    v1 = MagicMock()

    def _patch(name, namespace, body):
        if name == "sat-b":
            raise kubernetes.client.rest.ApiException(status=422, reason="Invalid")

    v1.patch_namespaced_pod.side_effect = _patch
    monkeypatch.setattr(wiring.kubernetes.config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(wiring.kubernetes.client, "CoreV1Api", lambda: v1)
    statuses = {"sat-a": _ready("sat-a", "uid-a"), "sat-b": _ready("sat-b", "uid-b")}
    handles = {
        "sat-a": _handle("sat-a", pod_uid="uid-a"),
        "sat-b": _handle("sat-b", pod_uid="uid-b"),
    }

    with pytest.raises(
        RuntimeError, match=r"failed for 1 pod\(s\): sat-b: pod sat-b uid=uid-b: HTTP 422"
    ):
        wiring.write_wiring_status(statuses, handles, namespace="nodalarc")


def test_write_wiring_status_refuses_a_proof_for_another_incarnation(monkeypatch, tmp_path) -> None:
    from unittest.mock import MagicMock

    from node_agent import wiring

    _kubelet_pods(monkeypatch, tmp_path, "uid-old", "uid-new")
    v1 = MagicMock()
    monkeypatch.setattr(wiring.kubernetes.config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(wiring.kubernetes.client, "CoreV1Api", lambda: v1)
    with pytest.raises(RuntimeError, match="proof names pod uid-old, handle names uid-new"):
        wiring.write_wiring_status(
            {"sat-a": _ready("sat-a", "uid-old")},
            {"sat-a": _handle("sat-a", pod_uid="uid-new")},
            namespace="nodalarc",
        )
    v1.patch_namespaced_pod.assert_not_called()


def test_pod_wiring_statuses_refuses_a_proof_for_another_node() -> None:
    from types import SimpleNamespace

    from nodalarc.substrate.wiring_status import (
        WIRING_STATUS_ANNOTATION,
        encode_status,
        pod_wiring_statuses,
    )
    from nodalarc.workload_target import NODE_ID_LABEL

    pod = SimpleNamespace(
        metadata=SimpleNamespace(
            labels={NODE_ID_LABEL: "sat-b"},
            annotations={WIRING_STATUS_ANNOTATION: encode_status(_ready("sat-a", "uid-a"))},
        )
    )
    with pytest.raises(ValueError, match="labelled 'sat-b' carries a wiring proof for 'sat-a'"):
        pod_wiring_statuses([pod], node_id_label=NODE_ID_LABEL)


def test_pod_without_proof_is_absent_and_malformed_proof_raises() -> None:
    from types import SimpleNamespace

    from nodalarc.substrate.wiring_status import WIRING_STATUS_ANNOTATION, pod_wiring_status

    assert pod_wiring_status(SimpleNamespace(metadata=SimpleNamespace(annotations=None))) is None
    with pytest.raises(ValueError):
        pod_wiring_status(
            SimpleNamespace(
                metadata=SimpleNamespace(annotations={WIRING_STATUS_ANNOTATION: "{not json"})
            )
        )
