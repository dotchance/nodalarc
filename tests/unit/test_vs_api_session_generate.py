"""Tests for VS-API session generation contract."""

from pathlib import Path

import yaml
from fastapi.testclient import TestClient
from vs_api.main import app

client = TestClient(app)


def _demo_session_with_name(name: str) -> str:
    raw = yaml.safe_load(Path("configs/sessions/demo-36-ospf.yaml").read_text())
    raw["session"]["name"] = name
    return yaml.dump(raw, default_flow_style=False)


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
    assert session["orbit"]["propagator"] == "j2-mean-elements"
    assert session["routing"]["protocol"] == "isis"
    assert session["routing"]["area_assignment"]["strategy"] == "per-plane"


def test_constellation_presets_expose_constellation_mode_for_wizard_gating():
    response = client.get("/api/v1/presets/constellations")

    assert response.status_code == 200
    presets = {item["name"]: item for item in response.json()}
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
