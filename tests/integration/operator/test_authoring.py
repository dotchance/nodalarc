# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""A user authors a session, and the session that runs is the one they authored.

Each test ends on the session it found and deletes the user catalog objects it created.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any

import pytest
import yaml
from nodalarc.models.builder_controls_api import (
    BuilderChoiceControl,
    BuilderControlTree,
    BuilderSequenceControl,
)

from tests.support.builder_controls import control_at, scalar_at

from .harness.client import Operator, Refused
from .harness.network import watch
from .truths import assert_session_is_truthful

pytestmark = [pytest.mark.integration, pytest.mark.timeout(2400)]


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


_FAMILY_ORDER = ("terminals", "nodes", "constellations", "sessions")


def _author_with_the_library_and_a_yaml_file(
    operator: Operator, created: list[str], name: str, found: dict[str, Any], shorter: int
) -> None:
    """Fork the terminal, the node and the constellation one by one, then upload a session."""
    forks = {
        found["terminal_ref"]: f"user:terminals/{name}.yaml",
        found["node_ref"]: f"user:nodes/{name}.yaml",
        found["constellation_ref"]: f"user:constellations/{name}.yaml",
    }
    session_ref = f"user:sessions/{name}.yaml"
    for leftover in (session_ref, *reversed(forks.values())):
        operator.delete_user_object(leftover)
    created.extend(forks.values())
    created.append(session_ref)
    operator.fork_catalog_object(
        found["terminal_ref"], forks[found["terminal_ref"]], {"/terminal/max_range_km": shorter}
    )
    operator.fork_catalog_object(
        found["node_ref"],
        forks[found["node_ref"]],
        {f"/node/terminals/{found['mount']}/terminal": forks[found["terminal_ref"]]},
    )
    operator.fork_catalog_object(
        found["constellation_ref"],
        forks[found["constellation_ref"]],
        {"/constellation/node": forks[found["node_ref"]]},
    )
    document = found["document"]
    found["segment"]["source"] = forks[found["constellation_ref"]]
    document["session"]["name"] = name
    accepted = operator.post(
        "/api/v1/session/deploy-from-yaml",
        {"yaml": yaml.safe_dump(document), "record_history": False},
    )
    operator.wait_for_session({"name": name}, accepted["operation_id"])


def _builder_save_request(draft: dict[str, Any]) -> dict[str, Any]:
    """The save request the page builds: the accepted session with its proposals,
    under the target and the fence the draft carries."""
    return {
        "draft": {
            "draft_revision": draft["draft_revision"],
            "state": {
                "session": draft["applied_session"],
                "catalog_documents": draft["catalog_documents"],
            },
        },
        "target_ref": draft["target_ref"],
        "expected_session_revision": draft["expected_session_revision"],
    }


def _builder_save(operator: Operator, draft: dict[str, Any]) -> dict[str, Any]:
    """Compile the accepted session for its verdict, as the page does, then save it."""
    request = _builder_save_request(draft)
    compiled = operator.post(
        "/api/v1/builder/compile",
        {"draft": request["draft"], "target_ref": request["target_ref"]},
    )
    blockers = compiled["save_verdict"]["blockers"]
    assert compiled["save_verdict"]["allowed"], "the Builder refuses to save:\n" + "\n".join(
        f"{blocker['code']}: {blocker['message']}" for blocker in blockers
    )
    return operator.post("/api/v1/builder/session/save", request)


def _author_in_the_builder(
    operator: Operator, created: list[str], name: str, found: dict[str, Any], shorter: int
) -> None:
    """Open the shipped session in the Builder, customize the chain down to the terminal,
    save, shorten the forked terminal's range, open the saved session again, save and deploy."""
    session_ref = f"user:sessions/{name}.yaml"
    leaf = f"user:terminals/{name}.yaml"
    operator.delete_user_object(session_ref)
    opened = operator.post(
        "/api/v1/builder/draft/open",
        {"source_ref": found["reference"]["source_id"]["session_ref"], "target_ref": session_ref},
    )
    assert opened["applied_session"] is not None, (
        f"the Builder did not open {found['reference']['name']}: {opened.get('issues')}"
    )
    customized = operator.post(
        "/api/v1/builder/draft/customize-chain",
        {
            "draft": opened,
            "expected_draft_revision": opened["draft_revision"],
            "segment_id": found["segment"]["id"],
            "leaf_ref": found["terminal_ref"],
            "target_leaf_ref": leaf,
        },
    )
    assert customized["applied"], f"the chain was not customized: {customized.get('issues')}"
    forked = sorted(
        (entry["target_ref"] for entry in customized["forked_chain"]),
        key=lambda ref: _FAMILY_ORDER.index(ref.split(":", 1)[1].split("/", 1)[0]),
    )
    print(f"the Builder forked {forked}")
    created.extend(forked)
    created.append(session_ref)
    _builder_save(operator, customized["draft"])

    operator.edit_catalog_object(leaf, {"/terminal/max_range_km": shorter})

    reopened = operator.post("/api/v1/builder/draft/open", {"source_ref": session_ref})
    saved = _builder_save(operator, reopened)
    operator.run_saved_session(saved)


@pytest.mark.parametrize(
    "author",
    [_author_with_the_library_and_a_yaml_file, _author_in_the_builder],
    ids=["the catalog library and a YAML file", "the Builder"],
)
def test_a_forked_terminal_with_a_shorter_range_limits_the_links_that_run(
    operator: Operator, created: list[str], author: Callable[..., None]
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
    found = {
        "reference": reference,
        "document": document,
        "segment": segment,
        "constellation_ref": constellation_ref,
        "node_ref": node_ref,
        "mount": mount,
        "terminal_ref": mounts[mount]["terminal"],
    }

    operator.run_session(reference)
    reference_ranges = _ground_link_ranges_km(operator)
    assert len(reference_ranges) >= 4, f"{reference['name']} shows too few ground links to compare"
    shorter = round(reference_ranges[len(reference_ranges) // 2])
    assert reference_ranges[-1] > shorter
    print(
        f"{reference['name']}: ground links from {reference_ranges[0]:.0f} to "
        f"{reference_ranges[-1]:.0f} km with {found['terminal_ref']}; forking it at {shorter} km"
    )

    name = "operator-test-fork"
    author(operator, created, name, found, shorter)

    assert operator.state()["constellation_name"] == name
    forked_ranges = _ground_link_ranges_km(operator)
    print(f"{name}: ground links from {forked_ranges[0]:.0f} to {forked_ranges[-1]:.0f} km")
    assert forked_ranges, "the forked session shows no ground link"
    assert forked_ranges[-1] <= shorter, (
        f"the forked terminal reaches {shorter} km and a ground link of "
        f"{forked_ranges[-1]:.0f} km is running"
    )
    assert_session_is_truthful(operator)


_EARTH = "nodalarc:bodies/earth.yaml"
_PROFILE = "nodalarc:profiles/frr-router.yaml"


def _segments_a_selector_names(selector: Any) -> set[str]:
    """The segments a selector selects when it is a segment predicate or a union of them.
    Any other selector fails the test, which does not read it."""
    if isinstance(selector, dict) and set(selector) == {"segment"}:
        return {selector["segment"]}
    if isinstance(selector, dict) and set(selector) == {"any"}:
        return set().union(*(_segments_a_selector_names(item) for item in selector["any"]))
    if isinstance(selector, list):
        return set().union(*(_segments_a_selector_names(item) for item in selector))
    raise AssertionError(f"not a union of segment predicates: {selector}")


def _segments_an_endpoint_lies_in(selector: Any) -> set[str] | None:
    """The segments an endpoint selector's nodes lie in; None when it names no segment."""
    if "segment" in selector:
        return {selector["segment"]}
    if "any" in selector:
        within = [_segments_an_endpoint_lies_in(item) for item in selector["any"]]
        return None if None in within else set().union(*within)
    if "all" in selector:
        within = [
            named
            for item in selector["all"]
            if (named := _segments_an_endpoint_lies_in(item)) is not None
        ]
        return set.intersection(*within) if within else None
    return None  # a tag, node or complement selector


def _isis_interfaces(printed: str) -> dict[str, set[str]]:
    """The interfaces of each IS-IS process a router runs, by area tag, from
    `show isis interface`."""
    areas: dict[str, set[str]] = {}
    area = None
    for line in printed.splitlines():
        if line.startswith("Area "):
            area = line.removeprefix("Area ").rstrip(":")
            areas[area] = set()
        elif area is not None and line.strip() and line.split()[0] != "Interface":
            areas[area].add(line.split()[0])
    return areas


def _earth_satellite_count(operator: Operator, constellation_ref: str) -> int | None:
    """How many satellites the constellation places, or None when it orbits another body."""
    constellation = operator.catalog_object(constellation_ref)["constellation"]
    if operator.catalog_object(constellation["orbit"])["orbit"]["central_body"] != _EARTH:
        return None
    return constellation["planes"]["count"] * constellation["slots_per_plane"]


def _a_shipped_earth_constellation_with_sites_it_reaches(
    operator: Operator,
) -> tuple[str, str, str]:
    """The smallest shipped Earth constellation, a site set a shipped session links it to,
    and that session's start time, when its sky puts the satellites over those sites."""
    pairs: set[tuple[str, str, str]] = set()
    for session in operator.sessions():
        if session["source"] != "nodalarc" or not session["deploy_allowed"]:
            continue
        document = yaml.safe_load(operator.session_yaml(session))
        constellations = {
            segment["id"]: segment["source"]
            for segment in document["segments"]
            if str(segment.get("source", "")).startswith("nodalarc:constellations/")
        }
        site_sets = {
            segment["id"]: segment["placement"]["from_site_set"]
            for segment in document["segments"]
            if "placement" in segment
        }
        for rule in document.get("link_rules", []):
            ends = [_segments_an_endpoint_lies_in(end["select"]) for end in rule["endpoints"]]
            for space, ground in (ends, ends[::-1]):
                if space is not None and ground is not None and len(space) == len(ground) == 1:
                    (space_id,), (ground_id,) = space, ground
                    if space_id in constellations and ground_id in site_sets:
                        pairs.add(
                            (
                                constellations[space_id],
                                site_sets[ground_id],
                                str(document["time"]["start_time"]),
                            )
                        )
    counted = [
        (count, constellation, site_set, start_time)
        for constellation, site_set, start_time in sorted(pairs)
        if (count := _earth_satellite_count(operator, constellation)) is not None
    ]
    assert counted, "no shipped session links an Earth constellation to a site set"
    _, constellation, site_set, start_time = min(counted)
    return constellation, site_set, start_time


def _session_controls(draft: dict[str, Any], segment_ids: tuple[str, ...]) -> dict[str, str]:
    """The control ids of the session's start time and of each segment's profile field."""
    root = BuilderControlTree.model_validate(draft["control_tree"]).root
    controls = {"start_time": scalar_at(root, "/time/start_time").control_id}
    segments = control_at(root, "/segments")
    assert isinstance(segments, BuilderSequenceControl)
    for item in segments.items:
        # Each segment item offers one branch per segment kind; the selected one is its own.
        assert isinstance(item.control, BuilderChoiceControl)
        branch = next(branch for branch in item.control.branches if branch.selected)
        assert branch.control is not None
        segment_id = scalar_at(branch.control, f"/segments/{item.index}/id").value
        if segment_id in segment_ids:
            controls[str(segment_id)] = scalar_at(
                branch.control, f"/segments/{item.index}/profile"
            ).control_id
    return controls


def test_a_session_built_in_the_builder_runs_the_rules_and_domains_it_authored(
    operator: Operator, created: list[str]
) -> None:
    """A user starts an empty Builder session, places a constellation and a site set,
    connects them, adds a routing domain, chooses each segment's profile and the start
    time, saves and deploys.

    Each authored id comes from the command that made it, and each domain's members and
    protocol from the saved session.
    """
    constellation_ref, site_set_ref, start_time = (
        _a_shipped_earth_constellation_with_sites_it_reaches(operator)
    )
    print(f"placing {constellation_ref} and {site_set_ref}, starting at {start_time}")
    name = "operator-test-builder-session"
    session_ref = f"user:sessions/{name}.yaml"
    operator.delete_user_object(session_ref)  # a leftover of an interrupted run
    created.append(session_ref)

    draft = operator.post("/api/v1/builder/draft/new", {"session_name": name})

    def command(body: dict[str, Any], kind: str) -> str:
        nonlocal draft
        result = operator.post(
            "/api/v1/builder/draft/command",
            {"draft": draft, "expected_draft_revision": draft["draft_revision"], "command": body},
        )
        assert result["affected_kind"] == kind, f"{body['operation']} made a {result}"
        assert not result["created_edits"], f"{body['operation']} accepted nothing: {result}"
        draft = result["draft"]
        return result["affected_id"]

    space = command(
        {"operation": "place_space_reference", "source_ref": constellation_ref}, "space"
    )
    ground = command(
        {"operation": "place_ground_reference", "site_set_ref": site_set_ref}, "ground"
    )
    # A session with link rules declares its candidate limits; the user states
    # them in the session window before connecting.
    limited = operator.post(
        "/api/v1/builder/draft/apply-edits",
        {
            "draft": draft,
            "expected_draft_revision": draft["draft_revision"],
            "edits": [
                {
                    "kind": "content",
                    "address": {"kind": "section", "pointer": "/simulation"},
                    "base": draft["applied_session"].get("simulation"),
                    "working": {
                        "candidate_limits": {
                            "max_pairs_per_rule": 2_000,
                            "max_pairs_per_tick": 10_000,
                        }
                    },
                }
            ],
        },
    )
    assert limited["applied"], f"the candidate limits were not accepted: {limited}"
    draft = limited["assembly"]["visual_draft"]
    rule = command(
        {"operation": "connect_segments", "from_segment_id": space, "to_segment_id": ground}, "link"
    )
    domain = command({"operation": "add_routing_domain"}, "routing_domain")

    controls = _session_controls(draft, (space, ground))
    edited = operator.post(
        "/api/v1/builder/draft/control-mutate",
        {
            "draft": draft,
            "expected_draft_revision": draft["draft_revision"],
            "commands": [
                {
                    "operation": "set_scalar",
                    "control_id": controls["start_time"],
                    "value": start_time,
                },
                *(
                    {"operation": "set_scalar", "control_id": controls[segment], "value": _PROFILE}
                    for segment in (space, ground)
                ),
            ],
        },
    )
    verdicts = edited["compile_result"]
    blockers = (
        verdicts["save_verdict"]["blockers"] + verdicts["deploy_eligibility_after_save"]["blockers"]
    )
    assert not blockers, "the Builder refuses the session it built:\n" + "\n".join(
        f"{blocker['code']}: {blocker['message']}" for blocker in blockers
    )
    saved = operator.post(
        "/api/v1/builder/session/save", _builder_save_request(edited["visual_draft"])
    )
    canonical = saved["session"]["canonical_json"]

    assert {segment["id"]: segment.get("profile") for segment in canonical["segments"]} == {
        space: _PROFILE,
        ground: _PROFILE,
    }
    assert [link_rule["id"] for link_rule in canonical["link_rules"]] == [rule]
    domains = {
        saved_domain["id"]: (
            saved_domain["protocol"],
            _segments_a_selector_names(saved_domain["selectors"]),
        )
        for saved_domain in canonical["routing"]["domains"]
    }
    assert set(domains) == {domain}
    # The command covers every placed segment.
    assert domains[domain][1] == {space, ground}, domains
    print(
        f"{name}: rule {rule}, "
        + ", ".join(
            f"domain {domain_id} {protocol} over {sorted(members)}"
            for domain_id, (protocol, members) in domains.items()
        )
        + f", profile {_PROFILE} on {space} and {ground}"
    )

    operator.run_saved_session(saved)
    heard = operator.watch_state(
        180.0,
        until=lambda messages: any(
            link["link_type"] == "ground" for link in messages[-1][1].get("links", [])
        ),
    )
    snapshots = [message for _, message in heard if "links" in message]
    assert snapshots, "the state feed delivered no snapshot"
    latest = snapshots[-1]

    print(
        f"links running per rule: {dict(Counter(link['link_rule_id'] for link in latest['links']))}"
    )
    running_rules = {link["link_rule_id"] for snapshot in snapshots for link in snapshot["links"]}
    assert running_rules <= {rule}, f"links run under rules other than {rule}: {running_rules}"
    assert any(link["link_type"] == "ground" for link in latest["links"]), (
        "no ground link ran within 180 s of the session being ready"
    )

    for domain_id, (protocol, members) in domains.items():
        in_domain = [node for node in latest["nodes"] if node["segment_id"] in members]
        assert {node["segment_id"] for node in in_domain} == members, (
            f"the session shows no node of {sorted(members - {n['segment_id'] for n in in_domain})}"
        )
        not_in_domain = [
            node["node_id"]
            for node in in_domain
            if (domain_id, protocol)
            not in {
                (instance["domain_id"], instance["protocol"])
                for instance in node["routing_instances"]
            }
        ]
        print(f"domain {domain_id}: {len(in_domain)} nodes, {len(not_in_domain)} outside it")
        assert not not_in_domain, (
            f"these nodes are not in {domain_id} ({protocol}): {not_in_domain}"
        )
        # Each router of the domain runs the protocol on the interfaces the session gives it.
        assert protocol == "isis", f"this test reads IS-IS routers; {domain_id} runs {protocol}"
        not_running = {}
        for node in in_domain:
            (instance,) = (i for i in node["routing_instances"] if i["domain_id"] == domain_id)
            declared = {interface["name"] for interface in instance["interfaces"]}
            running = _isis_interfaces(operator.introspect(node["node_id"], "show isis interface"))
            if not declared <= running.get(domain_id, set()):
                not_running[node["node_id"]] = (sorted(declared), running)
        print(f"domain {domain_id}: {len(in_domain) - len(not_running)} routers run {protocol}")
        assert not not_running, f"routers of {domain_id} not running {protocol}: {not_running}"
    # The routers' own neighbour tables over the shown ground links, and a ping from a
    # ground router: the protocol runs on the links the session shows.
    assert_session_is_truthful(operator)
