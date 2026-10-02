"""An operator selects a session, runs it, and switches to another.

Switching replaces the session running on the cluster. The test ends on the session it found.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from .harness.client import PROJECT_ROOT, Operator, Refused
from .truths import assert_session_is_truthful

pytestmark = [pytest.mark.integration, pytest.mark.timeout(2400)]

SHIPPED_SESSIONS = sorted(
    path.name for path in (PROJECT_ROOT / "catalog" / "nodalarc" / "sessions").glob("*.yaml")
)


def _running(operator: Operator) -> dict[str, Any]:
    running = [session for session in operator.sessions() if session.get("active")]
    assert len(running) == 1, f"the session list marks {len(running)} sessions as running"
    return running[0]


def _another(operator: Operator, running: dict[str, Any]) -> dict[str, Any]:
    return next(
        session
        for session in operator.sessions()
        if session["source"] == "nodalarc"
        and session["deploy_allowed"]
        and session["name"] != running["name"]
    )


def test_every_shipped_session_is_offered_and_the_running_one_is_marked(operator: Operator) -> None:
    refs = {session["source_id"].get("session_ref") for session in operator.sessions()}
    missing = [name for name in SHIPPED_SESSIONS if f"nodalarc:sessions/{name}" not in refs]
    assert not missing, f"shipped sessions the session list does not offer: {missing}"
    assert _running(operator)["name"] == operator.state()["constellation_name"]


def test_a_switch_the_list_did_not_offer_is_refused_and_the_session_keeps_running(
    operator: Operator,
) -> None:
    before = operator.state()["session_id"]
    other = _another(operator, _running(operator))
    with pytest.raises(Refused) as refusal:
        operator.switch(other, expected_document_digest=f"sha256:{'0' * 64}")
    print(f"refusal: {refusal.value.status} {refusal.value.body}")
    assert 400 <= refusal.value.status < 500
    assert refusal.value.body.get("message"), "the refusal carries no reason"
    after = operator.state()
    assert after["session_id"] == before and after["session_status"] == "ready"


@pytest.fixture
def first(operator: Operator) -> Iterator[dict[str, Any]]:
    """The session running before the test. It runs again afterward, whatever the test did."""
    session = _running(operator)
    yield session
    if operator.state()["constellation_name"] != session["name"]:
        operator.run_session(session)


def test_switching_runs_the_chosen_session_and_switching_back_restores_the_first(
    operator: Operator, first: dict[str, Any]
) -> None:
    second = _another(operator, first)
    first_run = operator.state()["session_id"]

    state = operator.run_session(second)
    print(
        f"switched {first['name']} -> {second['name']}: {len(state['nodes'])} nodes, {len(state['links'])} links"
    )
    assert state["session_id"] != first_run
    assert _running(operator)["name"] == second["name"]
    assert_session_is_truthful(operator)

    state = operator.run_session(first)
    print(
        f"switched {second['name']} -> {first['name']}: {len(state['nodes'])} nodes, {len(state['links'])} links"
    )
    assert _running(operator)["name"] == first["name"]
    assert_session_is_truthful(operator)
