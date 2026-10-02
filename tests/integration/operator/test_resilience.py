"""When part of the emulated network breaks under NodalArc, it says so and returns to the truth.

Each test breaks one thing outside NodalArc's API, then acts only as the operator. Select these
with `-m resilience`: they damage the running session on purpose and repair it.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from .harness.client import Operator
from .harness.faults import cut_ground_terminal
from .harness.network import ShownNetwork, link_interface, link_key, router_neighbors, watch
from .truths import assert_session_is_truthful

pytestmark = [pytest.mark.integration, pytest.mark.resilience, pytest.mark.timeout(1800)]


def _station_health(operator: Operator, ground: str) -> list[dict[str, Any]]:
    """What NodalArc shows about one ground station's actuation, per Scheduler."""
    return [
        entry
        for scheduler in operator.get("/api/v1/ops/health")["scheduler_instances"]
        for entry in scheduler["ground_stations"]
        if entry["gs_id"] == ground
    ]


def _wait_until(what: str, seconds: float, reached) -> None:
    deadline = time.monotonic() + seconds
    while not reached():
        assert time.monotonic() < deadline, f"{what} did not happen within {seconds:.0f} s"
        time.sleep(2.0)


@pytest.fixture
def session_left_working(operator: Operator, test_passed) -> Iterator[None]:
    """A resilience test that does not pass leaves a damaged session. It is deployed again."""
    running = next(session for session in operator.sessions() if session.get("active"))
    yield
    if not test_passed():
        operator.run_session(running)


def _cut_a_link_that_lasts(operator: Operator) -> tuple[str, str] | None:
    """Cut ground links, highest in the sky first, until NodalArc shows one station as faulted.

    NodalArc audits the kernel about once a minute. A link that the sky ends before the next
    audit takes its fault with it; that cut is not judged and the next link is cut.
    """
    shown = watch(operator, 6.0)
    elevation = {
        tuple(decision["pair"]): decision["elevation_deg"]
        for decision in operator.get("/api/v1/ground-link-decisions")["decisions"]
    }
    candidates = sorted(
        shown.routed_ground_links(),
        key=lambda found: (
            -max(
                elevation.get((found[0], found[1]), -90.0),
                elevation.get((found[1], found[0]), -90.0),
            )
        ),
    )
    for ground, satellite, link in candidates[:4]:
        if any(e["actuation_state"] != "clean" for e in _station_health(operator, ground)):
            continue
        interface = link_interface(link, ground)
        cut = cut_ground_terminal(ground, interface)
        print(f"cut {cut}, the server side of {ground} {interface} (linked to {satellite})")
        deadline = time.monotonic() + 150.0
        while time.monotonic() < deadline:
            if any(
                entry["actuation_state"] == "kernel_dirty"
                for entry in _station_health(operator, ground)
            ):
                return ground, interface
            still_linked = any(
                link_key(shown_link) == link_key(link) and shown_link["state"] == "active"
                for shown_link in operator.state()["links"]
            )
            if not still_linked:
                print(f"the sky ended {ground} <-> {satellite} before the kernel audit; not judged")
                break
            time.sleep(2.0)
        else:
            pytest.fail(
                f"{ground} {interface} was cut 150 s ago, NodalArc still shows the link active "
                f"and shows the station as {_station_health(operator, ground)}"
            )
    return None


def test_a_cut_ground_link_is_shown_as_a_fault_and_an_operator_repair_restores_the_truth(
    operator: Operator, session_left_working: None
) -> None:
    cut = _cut_a_link_that_lasts(operator)
    if cut is None:
        pytest.skip("did not run: every cut link was ended by the sky before a kernel audit")
    ground, interface = cut
    fault = _station_health(operator, ground)
    print(f"NodalArc shows: {fault}")
    state = operator.state()
    names = ShownNetwork([state], {}, {})
    with operator.terminal(ground) as terminal:
        neighbors = router_neighbors(terminal, names.nodes[ground], names.node_by_address())
    print(f"the router's neighbors while cut: {neighbors}")
    assert interface not in neighbors, (
        f"{ground} {interface} was cut and the router still holds a neighbor on it: {neighbors}"
    )

    intervention = f"operator-test-{uuid.uuid4().hex[:12]}"
    accepted = operator.post(
        "/api/v1/ops/repair",
        {
            "gs_id": ground,
            "reason": "operator test: repair a ground link cut on purpose",
            "intervention_id": intervention,
        },
    )
    assert accepted["status"] == "accepted", accepted

    _wait_until(
        f"NodalArc showing {ground} clean after the repair",
        180.0,
        lambda: (
            all(entry["actuation_state"] == "clean" for entry in _station_health(operator, ground))
            and not [n for n in operator.state()["actuation_notices"] if n.get("gs_id") == ground]
        ),
    )
    events = [
        event
        for event in operator.get("/api/v1/ops/events", limit=500, source="scheduler")
        if (event.get("details") or {}).get("intervention_id") == intervention
    ]
    codes = [event["code"] for event in events]
    print(f"repair events: {codes}")
    assert "OPERATOR_REPAIR_SUCCEEDED" in codes and "OPERATOR_REPAIR_FAILED" not in codes

    assert_session_is_truthful(operator)
