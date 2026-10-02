"""A user authors a session, and the session that runs is the one they authored.

Each test ends on the session it found and deletes the user catalog objects it created.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
import yaml

from .harness.client import Operator, Refused
from .harness.network import watch
from .truths import assert_session_is_truthful

pytestmark = [pytest.mark.integration, pytest.mark.timeout(2400)]


@pytest.fixture
def created(operator: Operator) -> Iterator[list[str]]:
    """User catalog refs the test creates. Afterward the found session runs and they are deleted."""
    found = next(session for session in operator.sessions() if session.get("active"))
    refs: list[str] = []
    yield refs
    if operator.state()["constellation_name"] != found["name"]:
        operator.run_session(found)
    for ref in reversed(refs):  # a session before the objects it refers to
        operator.delete_user_object(ref)


def _wizard_choices(operator: Operator) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The constellations the Wizard offers as available, smallest first, and its routing rules."""
    presets = operator.get("/api/v1/presets/constellations")["presets"]
    available = [preset for preset in presets if preset["capability"]["default_propagator"]]
    return sorted(available, key=lambda preset: preset["satellite_count"]), operator.get(
        "/api/v1/wizard/extensions"
    )


def _wizard_compile(
    operator: Operator, preset: dict[str, Any], rules: dict[str, Any], protocol: str
) -> dict[str, Any]:
    return operator.post(
        "/api/v1/builder/wizard/compile",
        {
            "draft_revision": 0,
            "intent": {
                "constellation_ref": preset["constellation"],
                "ground_site_set_ref": preset["ground_stations"],
                "orbit_propagator": preset["capability"]["default_propagator"],
                "protocol": protocol,
                "extensions": [],
                "area_strategy": "flat",
                "routing_timers": rules["routing_timer_defaults"],
            },
        },
    )


def test_every_constellation_the_wizard_offers_can_be_made_into_a_session(
    operator: Operator,
) -> None:
    presets, rules = _wizard_choices(operator)
    refused = []
    for preset in presets:
        for protocol in (protocol["id"] for protocol in rules["protocols"]):
            try:
                compiled = _wizard_compile(operator, preset, rules, protocol)
            except Refused as refusal:
                refused.append(
                    f"{preset['name']} with {protocol}: {refusal.body.get('message', refusal.body)}"
                )
                continue
            blockers = (
                compiled["save_verdict"]["blockers"]
                + compiled["deploy_eligibility_after_save"]["blockers"]
            )
            if blockers:
                refused.append(f"{preset['name']} with {protocol}: {blockers[0]['message']}")
    assert not refused, "the Wizard offers these and cannot build them:\n" + "\n".join(refused)


@pytest.mark.parametrize("protocol", ["isis", "ospf"])
def test_a_wizard_session_runs_with_the_protocol_the_user_chose(
    operator: Operator, created: list[str], protocol: str
) -> None:
    presets, rules = _wizard_choices(operator)
    assert protocol in {offered["id"] for offered in rules["protocols"]}, (
        f"the Wizard does not offer {protocol}"
    )
    compiled = None
    for preset in presets:
        try:
            compiled = _wizard_compile(operator, preset, rules, protocol)
            break
        except Refused:
            continue  # test_every_constellation_the_wizard_offers... reports the refusals
    assert compiled is not None, "the Wizard built none of the constellations it offers"

    saved = operator.save_session(compiled)
    created.append(saved["session"]["ref"])
    assert saved["session"]["canonical_yaml"] == compiled["canonical_session_yaml"]

    state = operator.run_saved_session(saved)
    protocols = {
        instance["protocol"] for node in state["nodes"] for instance in node["routing_instances"]
    }
    print(
        f"{state['constellation_name']}: {len(state['nodes'])} nodes, routing {sorted(protocols)}"
    )
    assert protocols == {protocol}
    assert_session_is_truthful(operator)


def _shipped_session_yaml(
    operator: Operator, name: str, having: Callable[[dict[str, Any]], bool] = lambda document: True
) -> dict[str, Any]:
    """The smallest shipped session `having` accepts, downloaded and renamed, as a user edits a file."""
    downloaded = [
        operator.session_yaml(session)
        for session in operator.sessions()
        if session["source"] == "nodalarc" and session["deploy_allowed"]
    ]
    document = yaml.safe_load(
        min((text for text in downloaded if having(yaml.safe_load(text))), key=len)
    )
    document["session"]["name"] = name
    return document


def test_a_session_uploaded_as_yaml_runs(operator: Operator, created: list[str]) -> None:
    name = "operator-test-upload"
    document = _shipped_session_yaml(operator, name)
    created.append(f"user:sessions/{name}.yaml")
    operator.delete_user_object(f"user:sessions/{name}.yaml")  # a leftover of an interrupted run

    accepted = operator.post(
        "/api/v1/session/deploy-from-yaml",
        {"yaml": yaml.safe_dump(document), "record_history": False},
    )
    state = operator.wait_for_session({"name": name}, accepted["operation_id"])
    assert state["constellation_name"] == name
    assert_session_is_truthful(operator)


def _unknown_field(document: dict[str, Any]) -> None:
    document["not_a_field"] = 1


def _bgp_domains(document: dict[str, Any]) -> None:
    for domain in document["routing"]["domains"]:
        domain["protocol"] = "bgp"
        domain.pop("area_assignment", None)  # areas are an IS-IS and OSPF field


@pytest.mark.parametrize(
    ("change", "cause"),
    [(_unknown_field, "not_a_field"), (_bgp_domains, "bgp")],
    ids=["a field the grammar does not have", "a protocol the runtime does not run yet"],
)
def test_an_uploaded_session_nodalarc_cannot_run_is_refused_with_its_cause_and_nothing_changes(
    operator: Operator, created: list[str], change: Callable[[dict[str, Any]], None], cause: str
) -> None:
    name = "operator-test-refused"
    document = _shipped_session_yaml(operator, name, having=lambda document: "routing" in document)
    change(document)
    created.append(f"user:sessions/{name}.yaml")
    running = operator.state()["session_id"]

    with pytest.raises(Refused) as refusal:
        operator.post(
            "/api/v1/session/deploy-from-yaml",
            {"yaml": yaml.safe_dump(document), "record_history": False},
        )
    print(f"{refusal.value.status} {refusal.value.body}")
    assert 400 <= refusal.value.status < 500
    assert operator.state()["session_id"] == running
    assert f"user:sessions/{name}.yaml" not in {
        session["source_id"].get("session_ref") for session in operator.sessions()
    }, "the refused session was saved to the user catalog"
    assert cause in str(refusal.value.body), (
        f"the refusal does not tell the user what is wrong ({cause}): {refusal.value.body}"
    )


def _ground_link_ranges_km(operator: Operator) -> list[float]:
    shown = watch(operator, 8.0)
    return sorted(link["range_km"] for _, _, link in shown.routed_ground_links())


def test_a_forked_terminal_with_a_shorter_range_limits_the_links_that_run(
    operator: Operator, created: list[str]
) -> None:
    """A user forks a satellite's access terminal, shortens its range and runs a session on it.

    The shipped session runs first as the reference. Both runs start at the session's start time,
    so they see the same sky.
    """
    reference = min(
        (
            session
            for session in operator.sessions()
            if session["source"] == "nodalarc" and session["deploy_allowed"]
        ),
        key=lambda session: len(operator.session_yaml(session)),
    )
    document = yaml.safe_load(operator.session_yaml(reference))
    segment = next(
        segment
        for segment in document["segments"]
        if str(segment.get("source", "")).startswith("nodalarc:constellations/")
    )
    constellation_ref = segment["source"]
    node_ref = operator.catalog_object(constellation_ref)["constellation"]["node"]
    mounts = operator.catalog_object(node_ref)["node"]["terminals"]
    mount = next(index for index, mount in enumerate(mounts) if mount["role"] == "access")
    terminal_ref = mounts[mount]["terminal"]

    operator.run_session(reference)
    reference_ranges = _ground_link_ranges_km(operator)
    assert len(reference_ranges) >= 4, f"{reference['name']} shows too few ground links to compare"
    shorter = round(reference_ranges[len(reference_ranges) // 2])
    assert reference_ranges[-1] > shorter
    print(
        f"{reference['name']}: ground links from {reference_ranges[0]:.0f} to "
        f"{reference_ranges[-1]:.0f} km with {terminal_ref}; forking it at {shorter} km"
    )

    name = "operator-test-fork"
    forks = {
        terminal_ref: f"user:terminals/{name}.yaml",
        node_ref: f"user:nodes/{name}.yaml",
        constellation_ref: f"user:constellations/{name}.yaml",
    }
    session_ref = f"user:sessions/{name}.yaml"
    for leftover in (session_ref, *reversed(forks.values())):
        operator.delete_user_object(leftover)
    created.extend(forks.values())
    created.append(session_ref)
    operator.fork_catalog_object(
        terminal_ref, forks[terminal_ref], {"/terminal/max_range_km": shorter}
    )
    operator.fork_catalog_object(
        node_ref, forks[node_ref], {f"/node/terminals/{mount}/terminal": forks[terminal_ref]}
    )
    operator.fork_catalog_object(
        constellation_ref, forks[constellation_ref], {"/constellation/node": forks[node_ref]}
    )
    segment["source"] = forks[constellation_ref]
    document["session"]["name"] = name

    accepted = operator.post(
        "/api/v1/session/deploy-from-yaml",
        {"yaml": yaml.safe_dump(document), "record_history": False},
    )
    operator.wait_for_session({"name": name}, accepted["operation_id"])
    forked_ranges = _ground_link_ranges_km(operator)
    print(f"{name}: ground links from {forked_ranges[0]:.0f} to {forked_ranges[-1]:.0f} km")
    assert forked_ranges, "the forked session shows no ground link"
    assert forked_ranges[-1] <= shorter, (
        f"the forked terminal reaches {shorter} km and a ground link of "
        f"{forked_ranges[-1]:.0f} km is running"
    )
    assert_session_is_truthful(operator)
