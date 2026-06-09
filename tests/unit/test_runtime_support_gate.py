# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The runtime-support gate is mandatory at resolve time.

Production never passes an explicit RuntimeSupport, so the resolver default
(Earth-Luna) must run the typed UnsupportedFeature layer unconditionally:
grammar-valid-but-unimplemented features fail at resolution — at upload or
deploy — never as untyped errors after a pod is already running.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from nodalarc.resolve_session import SessionResolutionError, resolve_session
from nodalarc.runtime_support import FeatureCategory, UnsupportedFeatureError

from tests.conftest import build_segment_session_dict


def _session(**kwargs) -> dict:
    defaults = {
        "name": "runtime-support-gate",
        "constellation": {"planes": {"count": 1, "sats_per_plane": 2}},
        "ground_stations": {"stations": ["a"]},
    }
    defaults.update(kwargs)
    return build_segment_session_dict(**defaults)


def _luna_body() -> dict:
    return {
        "body": {
            "id": "luna",
            "display_name": "Luna",
            "gravitational_parameter_km3_s2": 4902.800066,
            "mean_radius_km": 1737.4,
            "equatorial_radius_km": 1738.1,
            "polar_radius_km": 1736.0,
            "reference": "test-fixture",
        }
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _de440s_manifest(*, sha: str | None = None, coverage_end: str = "2026-07-01T00:00:00Z") -> dict:
    kernel_path = Path("configs/ephemerides/de440s.bsp")
    kernel = {
        "id": "de440s",
        "path": str(kernel_path),
        "targets": [_luna_body()],
        "frame": "gcrs",
        "coverage_start": "2026-06-01T00:00:00Z",
        "coverage_end": coverage_end,
        "sha256": sha if sha is not None else _sha256(kernel_path),
    }
    return {"provider": "skyfield_bsp", "quality_tier": "de440s", "kernels": [kernel]}


def _lunar_constellation(raw: dict) -> dict:
    orbit = raw["segments"][0]["source"]["constellation"]["orbit"]["orbit"]
    orbit["central_body"] = _luna_body()
    orbit["id"] = "luna-low-test"
    orbit["shape"] = {"altitude_km": 100}
    orbit["orientation"]["inclination_deg"] = 90
    return raw


class TestMandatoryGate:
    def test_bgp_routing_domain_rejected_typed_without_explicit_support(self) -> None:
        raw = _session(protocol="bgp")
        with pytest.raises(UnsupportedFeatureError) as err:
            resolve_session(raw)
        cats = {f.category for f in err.value.features}
        assert FeatureCategory.ROUTING_PROTOCOL in cats
        assert any(f.value == "bgp" for f in err.value.features)

    def test_unsupported_ephemeris_provider_rejected_typed_by_default(self) -> None:
        raw = _lunar_constellation(_session())
        raw["ephemeris"] = _de440s_manifest()
        raw["ephemeris"]["provider"] = "spice_kernel_stack"
        with pytest.raises(UnsupportedFeatureError) as err:
            resolve_session(raw)
        assert any(
            f.category == FeatureCategory.EPHEMERIS_PROVIDER and f.value == "spice_kernel_stack"
            for f in err.value.features
        )

    def test_static_routing_domain_is_supported(self) -> None:
        raw = _session(protocol="static")
        resolved = resolve_session(raw)
        assert {d.protocol for d in resolved.routing_domains} == {"static"}

    def test_luna_session_passes_default_earth_luna_gate(self) -> None:
        raw = _lunar_constellation(_session())
        for site in raw["segments"][1]["placement"]["from_site_set"]["site_set"]["sites"]:
            site["site"]["frame"]["body_fixed"]["body"] = _luna_body()
            site["site"]["location"] = {"lat_deg": -80.0, "lon_deg": 0.0, "alt_m": 0.0}
        raw["ephemeris"] = _de440s_manifest()
        resolved = resolve_session(raw)
        assert {n.central_body for n in resolved.nodes if n.kind == "satellite"} == {"luna"}


class TestCrossBodyAccess:
    def test_earth_ground_to_luna_satellite_access_rejected_at_resolve(self) -> None:
        # Ground sites stay on Earth; the constellation moves to Luna; the
        # default access rule pairs them — which must be rejected as
        # cross-body, never evaluated as mixed-frame geometry.
        raw = _lunar_constellation(_session())
        raw["ephemeris"] = _de440s_manifest()
        with pytest.raises(SessionResolutionError, match="body-local"):
            resolve_session(raw)


class TestEphemerisManifestAtResolve:
    def test_checksum_mismatch_fails_at_resolve(self) -> None:
        raw = _lunar_constellation(_session())
        for site in raw["segments"][1]["placement"]["from_site_set"]["site_set"]["sites"]:
            site["site"]["frame"]["body_fixed"]["body"] = _luna_body()
            site["site"]["location"] = {"lat_deg": -80.0, "lon_deg": 0.0, "alt_m": 0.0}
        raw["ephemeris"] = _de440s_manifest(sha="0" * 64)
        with pytest.raises(SessionResolutionError, match="checksum mismatch"):
            resolve_session(raw)

    def test_stale_coverage_fails_at_resolve(self) -> None:
        raw = _lunar_constellation(_session())
        for site in raw["segments"][1]["placement"]["from_site_set"]["site_set"]["sites"]:
            site["site"]["frame"]["body_fixed"]["body"] = _luna_body()
            site["site"]["location"] = {"lat_deg": -80.0, "lon_deg": 0.0, "alt_m": 0.0}
        manifest = _de440s_manifest(coverage_end="2026-06-02T00:00:00Z")
        manifest["kernels"][0]["coverage_start"] = "2026-06-01T00:00:00Z"
        raw["ephemeris"] = manifest
        # Session epoch is 2026-06-08 — outside the declared coverage window.
        with pytest.raises(SessionResolutionError, match="does not cover"):
            resolve_session(raw)
