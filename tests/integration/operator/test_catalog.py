"""Every session NodalArc ships runs, and shows the truth before and after its sky moves.

This is the long run. It replaces the session on the cluster once per shipped session and ends
on the session it found. Select it with `-m catalog`.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from .harness.client import PROJECT_ROOT, Operator
from .harness.network import let_the_sky_move
from .truths import assert_session_is_truthful, make_before_break_handover_holds_both_links

pytestmark = [pytest.mark.integration, pytest.mark.catalog, pytest.mark.timeout(2400)]

SHIPPED_SESSIONS = sorted(
    path.name for path in (PROJECT_ROOT / "catalog" / "nodalarc" / "sessions").glob("*.yaml")
)


@pytest.fixture(scope="module", autouse=True)
def session_found(operator: Operator) -> Iterator[None]:
    found = next(session for session in operator.sessions() if session.get("active"))
    yield
    if operator.state()["constellation_name"] != found["name"]:
        operator.run_session(found)


@pytest.mark.parametrize("file_name", SHIPPED_SESSIONS)
def test_a_shipped_session_runs_and_shows_the_truth(operator: Operator, file_name: str) -> None:
    offered = {session["source_id"].get("session_ref"): session for session in operator.sessions()}
    session = offered[f"nodalarc:sessions/{file_name}"]
    assert session["deploy_allowed"], f"NodalArc ships {file_name} and does not allow it to run"

    state = operator.run_session(session)
    print(
        f"{session['name']}: {len(state['nodes'])} nodes, {len(state['links'])} links, {state['routing_stack']}"
    )
    assert_session_is_truthful(operator)

    changes, started, ended = let_the_sky_move(operator)
    print(f"sky moved from {started} to {ended}: {len(changes)} ground link changes")
    if changes:
        assert_session_is_truthful(operator)

    handover = make_before_break_handover_holds_both_links(operator)
    if isinstance(handover, str):
        print(f"handover check did not run: {handover}")
    else:
        assert not handover, "\n".join(handover)
