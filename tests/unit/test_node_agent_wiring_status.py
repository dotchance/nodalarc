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
