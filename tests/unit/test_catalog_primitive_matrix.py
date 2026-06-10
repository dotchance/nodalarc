# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Primitive permutation matrix: every shipped primitive must COMPOSE.

Users build their own sessions from these primitives; a primitive that only
works inside the one shipped session that references it is not a building
block. This matrix composes real primitives with real primitives through the
production resolver:

- every constellation resolves into a session with its authored-compatible
  ground set (or fails with the typed UnsupportedFeature its future-gated
  propagator declares — never any other error class) and passes deploy-time
  readiness with zero errors,
- every orbit primitive is physically sane (perigee above its central
  body's surface, apogee >= perigee, bounded inclination),
- every site primitive — including the 130+ library sites no shipped set
  references yet — places into a session against a constellation matched to
  its authored terminal install, and resolves with zero readiness errors;
  optical-only sites (future segment content) may reject with the typed
  zero-compatible-mounts error, nothing else.

Geometry warnings (W005) are allowed here: the matrix pairs primitives
mechanically, and a polar library site under a mid-inclination shell is a
valid authoring combination for a user to reject — the shipped-session
contract is where pairings must also be geometrically live.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from nodalarc.models.resolved_session import SourceContext
from nodalarc.resolve_session import SessionResolutionError, resolve_session
from nodalarc.runtime_support import UnsupportedFeatureError
from nodalarc.session_validator import validate_session_readiness

from tests.conftest import build_segment_session_dict

CATALOG = Path(__file__).resolve().parents[2] / "catalog" / "nodalarc"
CONSTELLATIONS = sorted((CATALOG / "constellations").rglob("*.yaml"))
ORBITS = sorted((CATALOG / "orbits").rglob("*.yaml"))
SITES = sorted((CATALOG / "sites").rglob("*.yaml"))

# Authored compatibility: the ground set each shipped constellation is
# designed against (mirrors the shipped sessions).
CONSTELLATION_GROUND_SETS = {
    "earth-leo-ring-36": "nodalarc:site-sets/earth/leo/earth-leo-starlink-gateway-sites.yaml",
    "earth-leo-polar-36": "nodalarc:site-sets/earth/leo/earth-leo-polar-gateway-sites.yaml",
    "earth-leo-walker-delta-176": "nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml",
    "earth-meo-gps-24": "nodalarc:site-sets/earth/meo/earth-meo-gateway-sites.yaml",
    "earth-heo-molniya-3": "nodalarc:site-sets/earth/heo/earth-heo-gateway-sites.yaml",
    "earth-geo-ring-8": "nodalarc:site-sets/earth/geo/earth-geo-gateway-sites.yaml",
    "luna-polar-2": "nodalarc:site-sets/luna/luna-surface-sites.yaml",
    "luna-elfo-relay-2": "nodalarc:site-sets/luna/luna-surface-sites.yaml",
    "luna-nrho-relay-1": "nodalarc:site-sets/luna/luna-surface-sites.yaml",
}

LUNA_EPHEMERIS = {
    "provider": "skyfield_bsp",
    "quality_tier": "de440s",
    "kernels": [
        {
            "id": "de440s",
            "path": "configs/ephemerides/de440s.bsp",
            "sha256": "c1c7feeab882263fc493a9d5a5b2ddd71b54826cdf65d8d17a76126b260a49f2",
            "coverage_start": "1849-12-25T00:00:00Z",
            "coverage_end": "2150-01-21T00:00:00Z",
            "targets": [
                "nodalarc:bodies/earth.yaml",
                "nodalarc:bodies/luna.yaml",
            ],
            "frame": "gcrs",
        }
    ],
}


def _catalog_token(path: Path) -> str:
    return "nodalarc:" + str(path.relative_to(CATALOG))


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def _central_body_of_constellation(path: Path) -> str:
    orbit_ref = _load(path)["constellation"]["orbit"]
    orbit = _load(CATALOG / orbit_ref.removeprefix("nodalarc:"))["orbit"]
    return Path(orbit["central_body"].removeprefix("nodalarc:")).stem


def _resolve(raw: dict, run_id: str):
    return resolve_session(
        raw, source_context=SourceContext(origin="test.primitive_matrix", run_id=run_id)
    )


def _readiness_errors(resolved) -> list[str]:
    return [
        f.message
        for f in validate_session_readiness(resolved, available_node_count=3)
        if f.level == "error"
    ]


def test_primitive_inventory_is_nonempty() -> None:
    assert CONSTELLATIONS and ORBITS and SITES


def test_every_shipped_constellation_has_an_authored_ground_pairing() -> None:
    missing = sorted(
        path.stem for path in CONSTELLATIONS if path.stem not in CONSTELLATION_GROUND_SETS
    )
    assert missing == [], (
        f"new constellations need an authored ground pairing in this matrix: {missing}"
    )


@pytest.mark.parametrize("path", CONSTELLATIONS, ids=lambda p: p.stem)
def test_every_constellation_composes_into_a_session(path: Path) -> None:
    body = _central_body_of_constellation(path)
    raw = build_segment_session_dict(
        name=f"matrix-{path.stem}",
        constellation=_catalog_token(path),
        ground_stations=CONSTELLATION_GROUND_SETS[path.stem],
    )
    if body != "earth":
        raw["ephemeris"] = LUNA_EPHEMERIS

    try:
        resolved = _resolve(raw, "run-test-0050")
    except UnsupportedFeatureError as exc:
        assert exc.features, f"{path.name}: untyped UnsupportedFeatureError"
        return

    assert _readiness_errors(resolved) == []


@pytest.mark.parametrize("path", ORBITS, ids=lambda p: p.stem)
def test_every_orbit_primitive_is_physically_sane(path: Path) -> None:
    orbit = _load(path)["orbit"]
    body = _load(CATALOG / orbit["central_body"].removeprefix("nodalarc:"))["body"]
    radius = float(body["mean_radius_km"])
    assert radius > 0

    shape = orbit["shape"]
    if "altitude_km" in shape:
        perigee = apogee = float(shape["altitude_km"])
    else:
        perigee = float(shape["perigee_altitude_km"])
        apogee = float(shape["apogee_altitude_km"])
    assert perigee > 0, f"{path.name}: perigee below the surface"
    assert apogee >= perigee, f"{path.name}: apogee below perigee"

    inclination = float(orbit["orientation"]["inclination_deg"])
    assert 0.0 <= inclination <= 180.0, f"{path.name}: inclination out of range"


def _site_rf_access_install_km(site: dict) -> float | None:
    """The largest authored rf-access install range on the site, if any."""
    best: float | None = None
    for node in site.get("nodes", ()):
        for config in (node.get("terminals") or {}).values():
            caps = (config or {}).get("capabilities") or {}
            range_km = caps.get("max_range_km")
            if range_km is not None:
                best = max(best or 0.0, float(range_km))
    return best


def _constellation_for_site(body: str, lat_deg: float, install_km: float | None) -> str:
    if body == "luna":
        return "nodalarc:constellations/luna/llo/luna-polar-2.yaml"
    if install_km is None or install_km < 5000:
        return "nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml"
    if install_km < 30000:
        return "nodalarc:constellations/earth/meo/earth-meo-gps-24.yaml"
    if abs(lat_deg) > 60:
        return "nodalarc:constellations/earth/heo/earth-heo-molniya-3.yaml"
    return "nodalarc:constellations/earth/geo/earth-geo-ring-8.yaml"


@pytest.mark.parametrize("path", SITES, ids=lambda p: p.stem)
def test_every_site_places_into_a_session(path: Path) -> None:
    site = _load(path)["site"]
    body = Path(site["frame"]["body_fixed"]["body"].removeprefix("nodalarc:")).stem
    install = _site_rf_access_install_km(site)
    constellation = _constellation_for_site(body, float(site["location"]["lat_deg"]), install)
    raw = build_segment_session_dict(
        name=f"matrix-site-{site['id']}",
        constellation=constellation,
        ground_stations={
            "site_set": {
                "id": f"matrix-{site['id']}",
                "display_name": "matrix single-site set",
                "sites": [_catalog_token(path)],
            }
        },
    )
    if body != "earth":
        raw["ephemeris"] = LUNA_EPHEMERIS

    try:
        resolved = _resolve(raw, "run-test-0051")
    except SessionResolutionError as exc:
        # Optical-only / future-segment sites have no rf access mount and
        # must reject with the typed zero-compatible-mounts reason.
        assert "zero compatible mounts" in str(exc), f"{path.name}: {exc}"
        return

    assert _readiness_errors(resolved) == [], f"{path.name}"
