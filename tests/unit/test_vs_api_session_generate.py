"""Tests for VS-API session generation contract."""

from types import SimpleNamespace

import vs_api.main as main
import yaml
from fastapi.testclient import TestClient
from vs_api.main import app

from tests.conftest import build_segment_session_dict

client = TestClient(app)


def _demo_session_with_name(name: str) -> str:
    return yaml.dump(
        build_segment_session_dict(
            name=name,
            constellation={
                "planes": {"count": 1, "sats_per_plane": 2},
            },
            ground_stations={"stations": ["a"]},
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
            "constellation": "earth-leo-walker-delta-176",
            "protocol": "isis",
            "extensions": ["te", "mpls"],
            "area_strategy": "per_plane",
            "ground_stations": "nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml",
            "orbit_propagator": "j2_mean_elements",
        },
    )

    assert response.status_code == 200
    session = yaml.safe_load(response.json()["yaml"])
    assert "constellation" not in session
    assert "ground_stations" not in session
    assert "identity" not in session
    assert "kind" not in session["segments"][0]
    assert "kind" not in session["segments"][1]
    assert session["segments"][0]["source"].startswith("nodalarc:constellations/")
    assert session["segments"][1]["placement"]["from_site_set"].startswith("nodalarc:site-sets/")
    assert "orbit" not in session
    domain = session["routing"]["domains"][0]
    assert domain["protocol"] == "isis"
    assert domain["area_assignment"]["strategy"] == "per_plane"
    assert domain["capabilities"] == {"mpls": {}, "traffic_engineering": {}}


def test_constellation_presets_expose_constellation_mode_for_wizard_gating():
    response = client.get("/api/v1/presets/constellations")

    assert response.status_code == 200
    presets = {item["name"]: item for item in response.json()}
    assert presets["earth-leo-ring-36"]["mode"] == "constellation"
    assert presets["earth-leo-walker-delta-176"]["mode"] == "constellation"
    assert presets["earth-leo-polar-36"]["mode"] == "constellation"
    assert presets["earth-meo-gps-24"]["mode"] == "constellation"
    assert presets["earth-geo-ring-8"]["mode"] == "constellation"
    assert presets["earth-heo-molniya-3"]["mode"] == "constellation"
    assert presets["luna-polar-2"]["mode"] == "constellation"


def test_wizard_presets_are_catalog_backed_not_retired_config_roots():
    sat_response = client.get("/api/v1/presets/satellite-types")
    sets_response = client.get("/api/v1/presets/ground-stations")
    sites_response = client.get("/api/v1/presets/ground-stations/stations")

    assert sat_response.status_code == 200
    assert sat_response.json() == []
    assert sets_response.status_code == 200
    assert sites_response.status_code == 200

    site_sets = sets_response.json()
    sites = sites_response.json()
    assert site_sets
    assert sites
    assert all(item["file"].startswith("nodalarc:site-sets/") for item in site_sets)
    assert all(item["file"].startswith("nodalarc:sites/") for item in sites)


def test_wizard_extension_rules_use_catalog_area_strategy_tokens():
    response = client.get("/api/v1/wizard/extensions")

    assert response.status_code == 200
    assert response.json()["area_strategies"] == ["flat", "stripe", "per_plane"]


def test_generate_session_rejects_absolute_ground_station_reference():
    response = client.post(
        "/api/v1/session/generate",
        json={
            "constellation": "earth-leo-ring-36",
            "protocol": "isis",
            "extensions": ["te"],
            "ground_stations": "/tmp/outside",
            "orbit_propagator": "j2_mean_elements",
        },
    )

    assert response.status_code == 400
    assert "nodalarc:<path>" in response.json()["error"]


def test_preview_coverage_rejects_traversal_constellation_reference():
    response = client.post(
        "/api/v1/session/preview-coverage",
        json={
            "constellation": "nodalarc:../../outside.yaml",
            "ground_stations": "nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml",
        },
    )

    assert response.status_code == 400
    assert "traversal" in response.json()["error"]


def test_generate_session_rejects_satellite_type_path_syntax():
    response = client.post(
        "/api/v1/session/generate",
        json={
            "constellation": "earth-leo-ring-36",
            "protocol": "isis",
            "extensions": ["te"],
            "ground_stations": "nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml",
            "satellite_type": "../starlink-v2",
            "orbit_propagator": "j2_mean_elements",
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
    assert response.json()["error"] == "Invalid segment session YAML"


def test_deploy_from_yaml_rejects_session_name_with_path_separator():
    response = client.post(
        "/api/v1/session/deploy-from-yaml",
        json={"yaml": _demo_session_with_name("name/with/separators")},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "Invalid segment session YAML"


def test_deploy_writes_generated_session_outside_catalog_root(monkeypatch, tmp_path):
    captured: list[str] = []

    class FakeSessionManager:
        status = "ready"

        def rescan(self) -> None:
            captured.append("rescan")

    async def fake_run_switch(session_path: str) -> None:
        captured.append(session_path)

    monkeypatch.setattr(main, "_session_manager", FakeSessionManager())
    monkeypatch.setattr(
        main,
        "get_platform_config",
        lambda: SimpleNamespace(session_data_root=str(tmp_path)),
    )
    monkeypatch.setattr(main, "_run_switch", fake_run_switch)

    response = client.post(
        "/api/v1/session/deploy",
        json={"yaml": _demo_session_with_name("wizard-generated")},
    )

    assert response.status_code == 200
    session_file = response.json()["session_file"]
    assert session_file.startswith(str(tmp_path / "generated-sessions"))
    assert "/catalog/nodalarc/sessions/" not in session_file
    assert (tmp_path / "generated-sessions").is_dir()
    assert list((tmp_path / "generated-sessions").glob("_wizard-wizard-generated-*.yaml"))
    assert captured[0] == "rescan"


def test_upload_writes_generated_session_outside_catalog_root(monkeypatch, tmp_path):
    captured: list[str] = []

    class FakeSessionManager:
        status = "ready"

        def rescan(self) -> None:
            captured.append("rescan")

    async def fake_run_switch(session_path: str) -> None:
        captured.append(session_path)

    monkeypatch.setattr(main, "_session_manager", FakeSessionManager())
    monkeypatch.setattr(
        main,
        "get_platform_config",
        lambda: SimpleNamespace(session_data_root=str(tmp_path)),
    )
    monkeypatch.setattr(main, "_run_switch", fake_run_switch)

    response = client.post(
        "/api/v1/session/deploy-from-yaml",
        json={"yaml": _demo_session_with_name("uploaded-generated")},
    )

    assert response.status_code == 200
    session_file = response.json()["session_file"]
    assert session_file.startswith(str(tmp_path / "generated-sessions"))
    assert "/catalog/nodalarc/sessions/" not in session_file
    assert list((tmp_path / "generated-sessions").glob("_wizard-uploaded-generated-*.yaml"))
    assert captured[0] == "rescan"
