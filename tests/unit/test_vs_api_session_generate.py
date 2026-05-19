"""Tests for VS-API session generation contract."""

import yaml
from fastapi.testclient import TestClient
from vs_api.main import app

client = TestClient(app)


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
