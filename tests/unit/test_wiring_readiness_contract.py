"""One readiness rule for a wiring proof, applied identically by every consumer."""

from __future__ import annotations

import json
import subprocess

import pytest
from nodalarc.substrate.manifest_contract import REQUIRED_WIRING_PHASES, WiringManifest
from nodalarc.substrate.wiring_status import (
    READY_PHASE_JQ_CLAUSE,
    NodeWiringStatus,
    WiringPhaseResult,
    wiring_row,
)
from pydantic import ValidationError

from tests.unit.test_scheduler_wiring_gate import _manifest_dict

FIRST, SECOND = REQUIRED_WIRING_PHASES[0], REQUIRED_WIRING_PHASES[1]


def _manifest() -> WiringManifest:
    return WiringManifest.model_validate(_manifest_dict())


def _row(phases: list[dict], *, status: str = "ready") -> dict:
    manifest = _manifest()
    return {
        "node_id": "sat-a",
        "session_id": manifest.session_id,
        "session_run_id": manifest.session_run_id,
        "wiring_generation": manifest.wiring_generation,
        "pod_uid": "pod-sat-a",
        "sandbox_id": "sb-sat-a",
        "netns_id": "4026532100",
        "status": status,
        "phases": phases,
        "dirty_kernel": False,
    }


def _all_ready() -> list[dict]:
    return [{"phase": phase, "status": "ready"} for phase in REQUIRED_WIRING_PHASES]


def _shell_gate_accepts(row: dict) -> bool:
    """Evaluate the rendered release-gate phase clause with jq, as the init container does."""
    program = f'.status == "ready" and .dirty_kernel == false and {READY_PHASE_JQ_CLAUSE}'
    result = subprocess.run(
        ["jq", "-e", program], input=json.dumps(row), capture_output=True, text=True, check=False
    )
    assert result.returncode in (0, 1), result.stderr
    return result.returncode == 0


ROWS = {
    "complete": (_all_ready(), True),
    "empty": ([], False),
    "one required phase": ([{"phase": FIRST, "status": "ready"}], False),
    "one phase pending": (
        [
            {"phase": p, "status": "pending_pid" if p == SECOND else "ready"}
            for p in REQUIRED_WIRING_PHASES
        ],
        False,
    ),
}


@pytest.mark.parametrize("label", sorted(ROWS))
def test_well_formed_rows_get_one_answer_from_the_rule_and_the_shell_gate(label: str) -> None:
    phases, expected = ROWS[label]

    assert NodeWiringStatus.model_validate(_row(phases)).ready_for(_manifest()) is expected
    assert _shell_gate_accepts(_row(phases)) is expected


@pytest.mark.parametrize(
    ("phases", "fragment"),
    [
        (_all_ready() + [{"phase": "extra_step", "status": "failed"}], "unknown wiring phase"),
        (_all_ready() + [{"phase": FIRST, "status": "ready"}], "phases repeated"),
        (
            [{"phase": FIRST, "status": "failed"}, {"phase": FIRST, "status": "ready"}]
            + _all_ready()[1:],
            "phases repeated",
        ),
    ],
)
def test_unknown_or_repeated_phase_names_fail_validation_and_the_shell_gate(
    phases: list[dict], fragment: str
) -> None:
    with pytest.raises(ValidationError, match=fragment):
        NodeWiringStatus.model_validate(_row(phases))
    assert _shell_gate_accepts(_row(phases)) is False


@pytest.mark.parametrize(
    ("phases", "fragment"),
    [
        (
            {phase: {"phase": phase, "status": "ready"} for phase in REQUIRED_WIRING_PHASES},
            "list_type",
        ),
        (
            _all_ready()[:-1] + [{**_all_ready()[-1], "note": "extra key"}],
            "Extra inputs are not permitted",
        ),
        (_all_ready()[:-1] + [{**_all_ready()[-1], "error_message": None}], "string_type"),
        (_all_ready()[:-1] + ["pod_security"], "model_type"),
    ],
)
def test_malformed_phase_containers_and_records_are_refused_by_both(
    phases: object, fragment: str
) -> None:
    """The release gate refuses what the model refuses: a phases object instead
    of a list, a record with an extra key, a null message, a bare string."""
    with pytest.raises(ValidationError, match=fragment):
        NodeWiringStatus.model_validate(_row(phases))  # type: ignore[arg-type]
    assert _shell_gate_accepts(_row(phases)) is False  # type: ignore[arg-type]


def test_constructor_refuses_states_it_cannot_express() -> None:
    manifest = _manifest()
    for state in ("failed", "dirty_kernel", "pending_pid"):
        with pytest.raises(ValueError, match="state must be ready or wiring"):
            wiring_row("sat-a", manifest, pod_uid="p", sandbox_id="s", netns_id="n", state=state)  # type: ignore[arg-type]


def test_manifest_required_phases_must_be_unique() -> None:
    manifest = _manifest_dict()
    manifest["required_phases"] = list(REQUIRED_WIRING_PHASES) + [FIRST]

    with pytest.raises(ValidationError, match="required_phases repeated"):
        WiringManifest.model_validate(manifest)


def test_one_constructor_keeps_the_ready_and_wiring_distinction() -> None:
    manifest = _manifest()
    ready = wiring_row("sat-a", manifest, pod_uid="p", sandbox_id="s", netns_id="n", state="ready")
    wiring = wiring_row(
        "sat-a", manifest, pod_uid="p", sandbox_id="s", netns_id="n", state="wiring"
    )

    assert ready.status == "ready" and ready.ready_for(manifest)
    assert {phase.status for phase in ready.phases} == {"ready"}
    assert wiring.status == "wiring" and not wiring.ready_for(manifest)
    assert {phase.status for phase in wiring.phases} == {"pending_pid"}
    assert [phase.phase for phase in wiring.phases] == list(REQUIRED_WIRING_PHASES)


def test_rule_needs_the_manifest_identity() -> None:
    manifest = _manifest()
    row = NodeWiringStatus.model_validate(_row(_all_ready()))
    other = manifest.model_copy(update={"wiring_generation": "other-generation"})

    assert row.ready_for(manifest) is True
    assert row.ready_for(other) is False
    assert WiringPhaseResult(phase=FIRST, status="ready").phase == FIRST
