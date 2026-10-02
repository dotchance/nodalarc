"""Tests for VS-API session generation contract."""

from pathlib import Path

import pytest
import vs_api.main as main
import yaml
from vs_api.catalog_context import create_catalog_context
from vs_api.main import app

from tests.asgi_client import ASGITestClient as TestClient

client = TestClient(app)
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def catalog_client(tmp_path: Path):
    context = create_catalog_context(
        session_data_root=tmp_path,
        shipped_root=ROOT / "catalog/nodalarc",
    )
    app.dependency_overrides[main.get_catalog_context] = lambda: context
    try:
        yield TestClient(app), context
    finally:
        app.dependency_overrides.pop(main.get_catalog_context, None)


def _demo_session_with_name(name: str) -> str:
    raw = yaml.safe_load(
        (
            Path(__file__).resolve().parents[3] / "catalog/nodalarc/sessions/earth-leo-simple.yaml"
        ).read_text(encoding="utf-8")
    )
    raw["session"]["name"] = name
    return yaml.safe_dump(raw, default_flow_style=False, sort_keys=False)


def test_constellation_presets_expose_backend_runtime_capabilities(catalog_client):
    scoped_client, _context = catalog_client
    response = scoped_client.get("/api/v1/presets/constellations")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "presets",
        "custom_geometry",
        "custom_geometry_seed",
        "custom_geometry_default_node",
        "custom_geometry_patterns",
        "orbit_models",
    }
    presets = {item["name"]: item for item in payload["presets"]}

    earth = presets["earth-leo-ring-36"]["capability"]
    assert earth == {
        "source_kind": "constellation",
        "runtime_supported_propagators": ["j2_mean_elements", "two_body"],
        "default_propagator": "j2_mean_elements",
        "unavailable_reason": None,
    }
    assert presets["luna-polar-2"]["capability"]["default_propagator"] == "two_body"
    nrho = presets["luna-nrho-relay-1"]["capability"]
    assert nrho["runtime_supported_propagators"] == []
    assert nrho["default_propagator"] is None
    assert "crtbp" in nrho["unavailable_reason"]

    assert payload["custom_geometry"] == {
        "source_kind": "custom_geometry",
        "runtime_supported_propagators": ["j2_mean_elements", "two_body"],
        "default_propagator": "j2_mean_elements",
        "unavailable_reason": None,
    }
    assert payload["custom_geometry_seed"]["pattern"] == "walker_delta"
    assert payload["custom_geometry_seed"]["planes"] == 4
    assert payload["custom_geometry_default_node"].startswith("nodalarc:nodes/space/")
    assert [pattern["id"] for pattern in payload["custom_geometry_patterns"]] == [
        "walker_delta",
        "walker_star",
    ]
    assert [model["id"] for model in payload["orbit_models"]] == [
        "j2_mean_elements",
        "two_body",
        "sgp4_tle",
    ]


def test_wizard_presets_are_catalog_backed_not_retired_config_roots(catalog_client):
    scoped_client, _context = catalog_client
    # Satellite presets are catalog space-node PRIMITIVES (sessions assemble
    # from primitives — the constellation is geometry plus a default node,
    # and any catalog node can be composed in). The retired config-root
    # satellite-type overrides stay gone; this list must never be empty.
    sat_response = scoped_client.get("/api/v1/presets/satellite-types")
    sets_response = scoped_client.get("/api/v1/presets/ground-stations")
    sites_response = scoped_client.get("/api/v1/presets/ground-stations/stations")

    assert sat_response.status_code == 200
    sat_presets = sat_response.json()["presets"]
    assert sat_presets
    assert all(item["file"].startswith("nodalarc:nodes/space/") for item in sat_presets)
    assert all(item["terminals"] for item in sat_presets)
    assert sets_response.status_code == 200
    assert sites_response.status_code == 200

    site_sets = sets_response.json()["presets"]
    sites = sites_response.json()["stations"]
    assert site_sets
    assert sites
    assert all(item["file"].startswith("nodalarc:site-sets/") for item in site_sets)
    assert all(item["file"].startswith("nodalarc:sites/") for item in sites)


def test_wizard_extension_rules_use_catalog_area_strategy_tokens():
    response = client.get("/api/v1/wizard/extensions")

    assert response.status_code == 200
    payload = response.json()
    assert "area_strategies" not in payload
    assert {protocol["id"]: protocol["area_strategies"] for protocol in payload["protocols"]} == {
        "ospf": ["flat"],
        "isis": ["flat", "stripe", "per_plane"],
    }
    assert [protocol["id"] for protocol in payload["protocols"]] == ["ospf", "isis"]
    assert [extension["id"] for extension in payload["extensions"]] == ["te", "mpls", "sr"]
    assert all(protocol["extensions"] == ["sr", "te", "mpls"] for protocol in payload["protocols"])
    assert all(protocol["extension_constraints"] == {} for protocol in payload["protocols"])
    assert all(protocol["label"] and protocol["description"] for protocol in payload["protocols"])
    assert all(protocol["timer_fields"] for protocol in payload["protocols"])


def test_preview_coverage_rejects_traversal_constellation_reference():
    response = client.post(
        "/api/v1/session/preview-coverage",
        json={
            "intent": {
                "constellation_ref": "nodalarc:../../outside.yaml",
                "ground_site_set_ref": (
                    "nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml"
                ),
                "orbit_propagator": "j2_mean_elements",
            }
        },
    )

    assert response.status_code == 422
    assert "traversal" in response.text


def test_deploy_sanitizes_yaml_parser_errors(catalog_client):
    scoped_client, _context = catalog_client
    response = scoped_client.post(
        "/api/v1/session/deploy-from-yaml", json={"yaml": "session: [", "record_history": False}
    )

    assert response.status_code == 400
    assert response.json()["message"] == "Invalid session YAML"


def _upload(scoped_client, document: str):
    return scoped_client.post(
        "/api/v1/session/deploy-from-yaml", json={"yaml": document, "record_history": False}
    )


def _user_sessions(context) -> list[str]:
    snapshot = context.repository.snapshot(context.scope)
    return [str(document.ref) for document in snapshot.list(family="sessions", namespace="user")]


def test_deploy_rejects_session_name_with_path_separator(catalog_client):
    scoped_client, context = catalog_client
    response = _upload(scoped_client, _demo_session_with_name("../../outside"))

    print(response.status_code, response.json())
    assert response.status_code == 422
    assert response.json() == {
        "code": "catalog_closure.invalid_session_root",
        "message": (
            "Invalid persisted session root: session.name: "
            "String should match pattern '^[a-z0-9][a-z0-9_-]*$'"
        ),
        "cause_type": "ValidationError",
    }
    assert _user_sessions(context) == []


def test_an_upload_with_an_unknown_field_is_refused_naming_the_field(catalog_client):
    scoped_client, context = catalog_client
    raw = yaml.safe_load(_demo_session_with_name("unknown-field"))
    raw["not_a_field"] = 1

    response = _upload(scoped_client, yaml.safe_dump(raw, sort_keys=False))

    print(response.status_code, response.json())
    assert response.status_code == 422
    assert response.json() == {
        "code": "catalog_closure.invalid_session_root",
        "message": ("Invalid persisted session root: not_a_field: Extra inputs are not permitted"),
        "cause_type": "ValidationError",
    }
    assert _user_sessions(context) == []


def test_single_file_upload_requires_referenced_user_content(catalog_client, monkeypatch):
    scoped_client, context = catalog_client
    monkeypatch.setattr(main, "_available_session_node_count", lambda: 3)
    raw = yaml.safe_load(_demo_session_with_name("single-file-user-ref"))
    raw["segments"][0]["source"] = "user:constellations/not-uploaded.yaml"

    response = _upload(scoped_client, yaml.safe_dump(raw, sort_keys=False))

    print(response.status_code, response.json())
    assert response.status_code == 422
    assert response.json()["code"] == "catalog_closure.dangling_reference"
    assert "user:constellations/not-uploaded.yaml" in response.json()["message"]
    assert _user_sessions(context) == []


def test_an_upload_the_runtime_cannot_run_is_refused_and_not_saved(catalog_client, monkeypatch):
    scoped_client, context = catalog_client
    monkeypatch.setattr(main, "_available_session_node_count", lambda: 3)
    raw = yaml.safe_load(_demo_session_with_name("bgp-upload"))
    raw["routing"] = {
        "domains": [
            {"id": "all", "protocol": "bgp", "selectors": [{"segment": segment["id"]}]}
            for segment in raw["segments"][:1]
        ]
    }

    response = _upload(scoped_client, yaml.safe_dump(raw, sort_keys=False))

    print(response.status_code, response.json())
    assert response.status_code == 422
    assert response.json()["code"] == "runtime_support.unsupported"
    assert "bgp" in response.json()["message"]
    assert _user_sessions(context) == []


def test_an_upload_named_as_an_existing_user_session_is_refused(catalog_client, monkeypatch):
    scoped_client, context = catalog_client
    monkeypatch.setattr(main, "_available_session_node_count", lambda: 3)
    existing = _demo_session_with_name("already-here")
    transaction = context.repository.begin(context.scope)
    transaction.write_bytes(
        "user:sessions/already-here.yaml", existing.encode("utf-8"), expected_revision=None
    )
    transaction.commit()

    response = _upload(scoped_client, existing)

    print(response.status_code, response.json())
    assert response.status_code == 409
    assert response.json()["code"] == "catalog_repository.conflict"
    assert "user:sessions/already-here.yaml" in response.json()["message"]
