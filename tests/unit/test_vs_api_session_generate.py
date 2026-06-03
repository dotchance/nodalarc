"""Tests for VS-API session generation contract."""

import yaml
from fastapi.testclient import TestClient
from vs_api.main import app

from tests.conftest import build_segment_session_dict

client = TestClient(app)


def _demo_session_with_name(name: str) -> str:
    return yaml.dump(
        build_segment_session_dict(
            name=name,
            constellation="configs/constellations/demo-36.yaml",
            ground_stations="configs/ground-stations/sets/demo.yaml",
            protocol="ospf",
        ),
        default_flow_style=False,
        sort_keys=False,
    )


def test_generate_session_requires_orbit_propagator():
    response = client.post(
        "/api/v1/session/generate",
        json={
            "constellation": "starlink-176",
            "protocol": "isis",
            "extensions": ["te", "mpls"],
        },
    )

    assert response.status_code == 400
    assert response.json()["error"] == "orbit_propagator is required"


def test_generate_session_writes_selected_orbit_propagator():
    response = client.post(
        "/api/v1/session/generate",
        json={
            "constellation": "starlink-176",
            "protocol": "isis",
            "extensions": ["te", "mpls"],
            "area_strategy": "per-plane",
            "ground_stations": "configs/ground-stations/sets/starlink-176.yaml",
            "satellite_type": "starlink-v2",
            "orbit_propagator": "j2-mean-elements",
        },
    )

    assert response.status_code == 200
    session = yaml.safe_load(response.json()["yaml"])
    assert "constellation" not in session
    assert "ground_stations" not in session
    assert session["identity"]["mode"] == "segment_namespaced"
    assert session["segments"][0]["kind"] == "constellation"
    assert session["segments"][1]["kind"] == "ground_set"
    assert session["orbit"]["propagator"] == "j2-mean-elements"
    assert session["routing"]["protocol"] == "isis"
    assert session["routing"]["area_assignment"]["strategy"] == "per-plane"


def test_constellation_presets_expose_constellation_mode_for_wizard_gating():
    response = client.get("/api/v1/presets/constellations")

    assert response.status_code == 200
    presets = {item["name"]: item for item in response.json()}
    assert presets["demo-36"]["mode"] == "parametric"
    assert presets["leo-simple-36"]["mode"] == "parametric"
    assert presets["leo-walker-delta-176"]["mode"] == "parametric"
    assert presets["leo-polar-36"]["mode"] == "parametric"
    assert presets["geo-inmarsat-representative"]["mode"] == "explicit"
    assert presets["geo-tdrs-representative"]["mode"] == "explicit"
    assert presets["starlink-176"]["mode"] == "parametric"


def test_generate_session_rejects_absolute_ground_station_reference():
    response = client.post(
        "/api/v1/session/generate",
        json={
            "constellation": "starlink-176",
            "protocol": "isis",
            "extensions": ["te"],
            "ground_stations": "/tmp/outside",
            "orbit_propagator": "j2-mean-elements",
        },
    )

    assert response.status_code == 400
    assert "absolute" in response.json()["error"]


def test_preview_coverage_rejects_traversal_constellation_reference():
    response = client.post(
        "/api/v1/session/preview-coverage",
        json={
            "constellation": "../../outside",
            "satellite_type": "starlink-v2",
            "ground_stations": "global",
        },
    )

    assert response.status_code == 400
    assert "traversal" in response.json()["error"]


def test_generate_session_rejects_satellite_type_path_syntax():
    response = client.post(
        "/api/v1/session/generate",
        json={
            "constellation": "starlink-176",
            "protocol": "isis",
            "extensions": ["te"],
            "ground_stations": "global",
            "satellite_type": "../starlink-v2",
            "orbit_propagator": "j2-mean-elements",
        },
    )

    assert response.status_code == 400
    assert "satellite_type" in response.json()["error"]


def test_deploy_sanitizes_yaml_parser_errors():
    response = client.post("/api/v1/session/deploy", json={"yaml": "session: ["})

    assert response.status_code == 400
    assert response.json()["error"] == "Invalid session YAML"


def test_deploy_rejects_session_name_with_path_separator():
    response = client.post(
        "/api/v1/session/deploy",
        json={"yaml": _demo_session_with_name("../../outside")},
    )

    assert response.status_code == 400
    assert "path separators" in response.json()["error"]


def test_deploy_from_yaml_rejects_session_name_with_path_separator():
    response = client.post(
        "/api/v1/session/deploy-from-yaml",
        json={"yaml": _demo_session_with_name("name/with/separators")},
    )

    assert response.status_code == 400
    assert "path separators" in response.json()["error"]
