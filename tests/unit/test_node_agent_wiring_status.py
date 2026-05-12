import pytest
from node_agent.manifest_contract import REQUIRED_WIRING_PHASES, WiringManifest
from node_agent.wiring_status import failed_status


def _manifest() -> WiringManifest:
    return WiringManifest.model_validate(
        {
            "session_id": "demo",
            "wiring_generation": "sha256:" + "a" * 64,
            "required_phases": list(REQUIRED_WIRING_PHASES),
            "nodes": {
                "sat-a": {
                    "node_type": "satellite",
                    "plane": 0,
                    "slot": 0,
                    "sysctls": {"net.ipv6.conf.all.forwarding": "1"},
                    "isl_interfaces": [],
                    "gnd_interfaces": [],
                    "mpls_enable": True,
                    "segment_routing": False,
                    "mtu": 9000,
                    "remove_default_route": True,
                }
            },
            "ground_bridges": {},
            "isl_link_count": 0,
        }
    )


def test_failed_status_marks_prior_ready_failed_phase_dirty_and_later_pending() -> None:
    status = failed_status(
        "sat-a",
        _manifest(),
        phase="ground_infrastructure",
        error_message="bridge failed",
        dirty_kernel=True,
    )

    phases = {phase.phase: phase for phase in status.phases}
    assert phases["phase0_cleanup"].status == "ready"
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
            phase="not_a_phase",
            error_message="bad phase",
        )
