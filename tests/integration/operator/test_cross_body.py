# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""A ground station links to an orbiter of another body, and a direct link between the surfaces
of two bodies is refused.

The user forks the shipped lunar DTN relay vehicle in the Builder library and adds an access
terminal for Earth stations, so one bus serves lunar stations and an Earth station at once. A copy
of earth-luna-dtn points the Earth DTN station's access rule at that terminal, runs the Earth DTN
stations in the lunar IS-IS domain, and starts while the relay is in view of Tidbinbilla
(2026-06-08 16:30 to 2026-06-09 01:50 UTC). No shipped terminal reaches the Moon, so the copy
uses the geometry-only ground link model, which enforces line of sight and the elevation mask.
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml

from .harness.client import Operator, Refused
from .harness.network import watch
from .truths import assert_session_is_truthful

pytestmark = [pytest.mark.integration, pytest.mark.timeout(2400)]

VEHICLE = "nodalarc:nodes/space/luna-dtn-relay-vehicle.yaml"
RELAY_SET = "nodalarc:space-node-sets/luna/luna-dtn-relay-1.yaml"
EARTH_TERMINAL = "nodalarc:terminals/rf/rf-ka-geo-access.yaml"
RELAY = "luna-relay-sat-p00s00"


def _earth_luna_dtn(operator: Operator, name: str) -> dict[str, Any]:
    shipped = next(
        session for session in operator.sessions() if session["name"] == "earth-luna-dtn"
    )
    document = yaml.safe_load(operator.session_yaml(shipped))
    document["session"]["name"] = name
    return document


def _rule(document: dict[str, Any], rule_id: str) -> dict[str, Any]:
    return next(rule for rule in document["link_rules"] if rule["id"] == rule_id)


def _by_mount(mount: str) -> dict[str, Any]:
    return {"all": [{"role": "access"}, {"mount": mount}]}


def _fork_vehicle_with_an_earth_terminal(operator: Operator, target: str) -> str:
    """Fork the relay vehicle and add an access terminal for Earth stations, as the Builder library
    does. Returns the new mount's id."""
    draft = operator.post(
        "/api/v1/builder/catalog/draft/open", {"source_ref": VEHICLE, "target_ref": target}
    )
    added = operator.post(
        "/api/v1/builder/catalog/draft/node-terminal/add",
        {
            "draft": draft["state"],
            "expected_draft_revision": draft["state"]["draft_revision"],
            "terminal_ref": EARTH_TERMINAL,
            "role": "access",
            "boresight": {"mode": "nadir"},
        },
    )
    compiled = operator.post(
        "/api/v1/builder/catalog/draft/compile",
        {"draft": added["state"], "expected_draft_revision": added["state"]["draft_revision"]},
    )
    assert compiled["save_allowed"], f"the vehicle fork cannot be saved: {compiled['issues']}"
    operator.post(
        "/api/v1/builder/catalog/draft/save",
        {
            "draft": compiled["draft"]["state"],
            "expected_draft_revision": compiled["draft"]["state"]["draft_revision"],
        },
    )
    mounts = operator.catalog_object(target)["node"]["terminals"]
    (earth,) = [m for m in mounts if m["role"] == "access" and m["terminal"] == EARTH_TERMINAL]
    return str(earth["id"])


def test_a_direct_link_between_two_bodies_surfaces_is_refused_with_its_rule(
    operator: Operator, created: list[str]
) -> None:
    name = "operator-test-surface-to-surface"
    document = _earth_luna_dtn(operator, name)
    lunar = _rule(document, "luna_access")
    lunar["endpoints"][1] = {
        "select": {"all": [{"segment": "dtn_earth_ground"}, {"tag": "leo"}]},
        "terminal": lunar["endpoints"][1]["terminal"],
    }
    document["link_rules"].remove(_rule(document, "dtn_earth_access"))
    created.append(f"user:sessions/{name}.yaml")
    running = operator.state()["session_id"]

    with pytest.raises(Refused) as refusal:
        operator.post(
            "/api/v1/session/deploy-from-yaml",
            {"yaml": yaml.safe_dump(document), "record_history": False},
        )

    print(f"{refusal.value.status} {refusal.value.body}")
    assert 400 <= refusal.value.status < 500
    assert "surface_to_surface_cross_body" in str(refusal.value.body)
    assert "luna_access" in str(refusal.value.body)
    assert operator.state()["session_id"] == running


def test_an_earth_station_carries_traffic_to_the_moon_through_a_lunar_orbiter(
    operator: Operator, created: list[str]
) -> None:
    name = "operator-test-cross-body"
    vehicle = f"user:nodes/{name}-relay.yaml"
    relay_set = f"user:space-node-sets/{name}-relay.yaml"
    session_ref = f"user:sessions/{name}.yaml"
    for leftover in (session_ref, relay_set, vehicle):
        operator.delete_user_object(leftover)
    created.extend([vehicle, relay_set, session_ref])

    earth_mount = _fork_vehicle_with_an_earth_terminal(operator, vehicle)
    operator.fork_catalog_object(RELAY_SET, relay_set, {"/space_node_set/nodes/0/node": vehicle})

    document = _earth_luna_dtn(operator, name)
    next(seg for seg in document["segments"] if seg["id"] == "luna_relay")["source"] = relay_set
    for rule_id in ("luna_access", "dtn_luna_access"):
        _rule(document, rule_id)["endpoints"][1]["terminal"] = _by_mount("access_s")
    earth = _rule(document, "dtn_earth_access")
    earth["endpoints"][1] = {
        "select": {"segment": "luna_relay"},
        "terminal": _by_mount(earth_mount),
    }
    domains = {domain["id"]: domain for domain in document["routing"]["domains"]}
    dtn_earth = {"all": [{"segment": "dtn_earth_ground"}, {"tag": "leo"}]}
    domains["earth_domain"]["selectors"][0]["any"].remove(dtn_earth)
    domains["luna_domain"]["selectors"][0]["any"].append(dtn_earth)
    document["simulation"]["ground_link_model"] = "geometry_only"
    document["simulation"]["acknowledge_geometry_only"] = True
    document["time"]["start_time"] = "2026-06-08T17:00:00Z"

    accepted = operator.post(
        "/api/v1/session/deploy-from-yaml",
        {"yaml": yaml.safe_dump(document), "record_history": False},
    )
    operator.wait_for_session({"name": name}, accepted["operation_id"])

    shown = watch(operator, 10.0)
    cross_body = [
        link
        for _, _, link in shown.routed_ground_links()
        if RELAY in (link["node_a"], link["node_b"])
        and any(node.startswith("earth-") for node in (link["node_a"], link["node_b"]))
    ]
    assert cross_body, "no ground link joins an Earth station and the lunar relay"
    for link in cross_body:
        print(
            f"{link['node_a']} <-> {link['node_b']}: {link['range_km']:.0f} km, "
            f"{link['latency_a_to_b_ms']:.3f} / {link['latency_b_to_a_ms']:.3f} ms"
        )
        assert 350_000.0 < link["range_km"] < 410_000.0
        assert 1_150.0 < link["latency_a_to_b_ms"] < 1_400.0
        assert 1_150.0 < link["latency_b_to_a_ms"] < 1_400.0
    assert_session_is_truthful(operator)
