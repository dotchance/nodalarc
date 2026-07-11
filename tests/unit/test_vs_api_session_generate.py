"""Tests for VS-API session generation contract."""

from pathlib import Path

import pytest
import vs_api.main as main
import yaml
from nodalarc.catalog_refs import SessionRef
from vs_api.builder_compiler import canonicalize_persisted_configuration
from vs_api.catalog_context import create_catalog_context
from vs_api.main import app

from tests.asgi_client import ASGITestClient as TestClient

client = TestClient(app)
ROOT = Path(__file__).resolve().parents[2]


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
            Path(__file__).resolve().parents[2] / "catalog/nodalarc/sessions/earth-leo-simple.yaml"
        ).read_text(encoding="utf-8")
    )
    raw["session"]["name"] = name
    return yaml.safe_dump(raw, default_flow_style=False, sort_keys=False)


def test_legacy_generate_endpoint_is_retired():
    response = client.post(
        "/api/v1/session/generate",
        json={"constellation": "retired", "protocol": "isis"},
    )

    assert response.status_code == 404


def test_constellation_presets_expose_backend_runtime_capabilities():
    response = client.get("/api/v1/presets/constellations")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "presets",
        "custom_geometry",
        "custom_geometry_seed",
        "custom_geometry_default_node",
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
    assert [model["id"] for model in payload["orbit_models"]] == [
        "j2_mean_elements",
        "two_body",
        "sgp4_tle",
    ]


def test_constellation_preset_openapi_uses_generated_response_contract():
    operation = app.openapi()["paths"]["/api/v1/presets/constellations"]["get"]

    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/WizardConstellationPresetResponse"
    }


def test_wizard_presets_are_catalog_backed_not_retired_config_roots():
    # Satellite presets are catalog space-node PRIMITIVES (sessions assemble
    # from primitives — the constellation is geometry plus a default node,
    # and any catalog node can be composed in). The retired config-root
    # satellite-type overrides stay gone; this list must never be empty.
    sat_response = client.get("/api/v1/presets/satellite-types")
    sets_response = client.get("/api/v1/presets/ground-stations")
    sites_response = client.get("/api/v1/presets/ground-stations/stations")

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
    assert response.json()["area_strategies"] == ["flat", "stripe", "per_plane"]
    assert response.json()["protocols"] == {
        "ospf": {"extensions": ["sr", "te", "mpls"], "constraints": {}},
        "isis": {"extensions": ["sr", "te", "mpls"], "constraints": {}},
    }


def test_wizard_data_endpoints_publish_closed_response_models():
    paths = {
        "/api/v1/presets/satellite-types": "WizardSatelliteTypePresetResponse",
        "/api/v1/presets/ground-stations": "WizardGroundStationSetPresetResponse",
        "/api/v1/presets/ground-stations/stations": "WizardAvailableStationResponse",
        "/api/v1/wizard/extensions": "WizardExtensionRulesResponse",
    }
    schema = app.openapi()["paths"]

    for path, response_model in paths.items():
        operation = schema[path]["get"]
        assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
            "$ref": f"#/components/schemas/{response_model}"
        }


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


def test_preview_coverage_openapi_uses_generated_request_and_response_contracts():
    operation = app.openapi()["paths"]["/api/v1/session/preview-coverage"]["post"]

    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/WizardCoverageRequest"
    }
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/CoveragePreviewResult"
    }


def test_deploy_sanitizes_yaml_parser_errors(catalog_client):
    scoped_client, _context = catalog_client
    response = scoped_client.post("/api/v1/session/deploy-from-yaml", json={"yaml": "session: ["})

    assert response.status_code == 400
    assert response.json()["error"] == "Invalid session YAML"


def test_deploy_rejects_session_name_with_path_separator(catalog_client):
    scoped_client, _context = catalog_client
    response = scoped_client.post(
        "/api/v1/session/deploy-from-yaml",
        json={"yaml": _demo_session_with_name("../../outside")},
    )

    assert response.status_code == 422
    assert "ref-composed published grammar" in response.json()["error"]


def test_legacy_deploy_alias_is_retired():
    response = client.post("/api/v1/session/deploy", json={"yaml": "session: {}"})

    assert response.status_code == 404


def test_single_file_upload_requires_referenced_user_content(catalog_client):
    scoped_client, _context = catalog_client
    raw = yaml.safe_load(
        (
            Path(__file__).resolve().parents[2] / "catalog/nodalarc/sessions/earth-leo-simple.yaml"
        ).read_text(encoding="utf-8")
    )
    raw["session"]["name"] = "single-file-user-ref"
    raw["segments"][0]["source"] = "user:constellations/not-uploaded.yaml"

    response = scoped_client.post(
        "/api/v1/session/deploy-from-yaml",
        json={"yaml": yaml.safe_dump(raw, sort_keys=False)},
    )

    assert response.status_code == 422
    assert "complete closure" in response.json()["error"]


def test_upload_saves_canonical_user_catalog_session_and_admits_catalog_deploy(
    monkeypatch,
    catalog_client,
):
    scoped_client, context = catalog_client
    captured: list[object] = []

    async def fake_admit(worker, *, reservation):
        captured.append(reservation)
        return "upload-operation-2"

    monkeypatch.setattr(main, "_session_manager", object())
    monkeypatch.setattr(main, "_available_session_node_count", lambda: 7)
    monkeypatch.setattr(main, "_prepared_transition_reservation", lambda _deployment: "proof")
    monkeypatch.setattr(main, "_admit_transition", fake_admit)

    uploaded_yaml = _demo_session_with_name("uploaded-generated")
    response = scoped_client.post(
        "/api/v1/session/deploy-from-yaml",
        json={"yaml": uploaded_yaml},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "accepted",
        "operation_id": "upload-operation-2",
        "source": {
            "kind": "catalog",
            "session_ref": "user:sessions/uploaded-generated.yaml",
        },
    }
    saved = context.repository.snapshot(context.scope).get("user:sessions/uploaded-generated.yaml")
    expected = canonicalize_persisted_configuration(
        SessionRef("user:sessions/uploaded-generated.yaml"),
        yaml.safe_load(uploaded_yaml),
    )
    assert saved.content == expected.yaml_bytes
    assert saved.content != uploaded_yaml.encode("utf-8")
    assert captured == ["proof"]
