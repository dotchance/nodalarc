# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Tests for session_validator — pre-deployment validation checks."""

from __future__ import annotations

import pytest
import yaml
from nodalarc.constellation_loader import (
    SatelliteNode,
    expand_constellation,
    load_constellation,
    load_ground_stations,
)
from nodalarc.models.constellation import (
    GroundTerminal,
    IslTerminal,
    OrbitParams,
    ParametricConstellation,
    PlaneParams,
    TerminalConfig,
    TLEConstellation,
)
from nodalarc.models.events import ValidationResult
from nodalarc.models.ground_station import (
    GroundStationConfig,
    GroundStationFile,
    GroundTerminalDef,
)
from nodalarc.models.session import (
    OrbitConfig,
    PlacementConfig,
    RoutingConfig,
    SessionConfig,
    SessionMeta,
    TimeConfig,
)
from nodalarc.orbital import elements_from_params
from nodalarc.session_validator import (
    VALID_SCHEDULING_POLICIES,
    build_validation_report,
    validate_session_readiness,
)
from nodalarc.stack_resolver import ResolvedStack, resolve_stack

from tests.conftest import CONFIGS_DIR

ISS_TLE_LINE_1 = "1 25544U 98067A   21075.51041667  .00001264  00000-0  29660-4 0  9993"
ISS_TLE_LINE_2 = "2 25544  51.6442  21.5417 0002426  95.1670  21.8444 15.48974333273145"

# ---------------------------------------------------------------------------
# Helpers — build minimal valid models for testing
# ---------------------------------------------------------------------------


def _make_session(
    *,
    protocol: str = "isis",
    extensions: list[str] | None = None,
    step_seconds: int = 1,
    bfd: bool = False,
    bfd_rx_interval: int = 300,
    placement_policy: str = "allOnOne",
    planes_per_group: int | None = None,
) -> SessionConfig:
    return SessionConfig(
        session=SessionMeta(name="test-session"),
        constellation="configs/constellations/demo-36.yaml",
        ground_stations="configs/ground-stations/sets/demo.yaml",
        orbit=OrbitConfig(propagator="keplerian-circular"),
        routing=RoutingConfig(
            protocol=protocol,
            extensions=extensions or [],
            bfd=bfd,
            bfd_rx_interval=bfd_rx_interval,
        ),
        time=TimeConfig(step_seconds=step_seconds),
        placement=PlacementConfig(
            policy=placement_policy,
            planes_per_group=planes_per_group,
        ),
    )


def _make_gs_file(
    stations: list[GroundStationConfig] | None = None,
    default_scheduling_policy: str = "highest-elevation",
) -> GroundStationFile:
    if stations is None:
        stations = [
            GroundStationConfig(
                name="test-gs",
                lat_deg=34.0,
                lon_deg=-118.0,
                terminals=[
                    GroundTerminalDef(
                        type="rf",
                        count=2,
                        bandwidth_mbps=1000,
                        tracking_capacity=2,
                    )
                ],
            ),
        ]
    return GroundStationFile(
        default_terminals=[
            GroundTerminalDef(
                type="rf",
                count=1,
                bandwidth_mbps=1000,
                tracking_capacity=1,
            )
        ],
        default_scheduling_policy=default_scheduling_policy,
        stations=stations,
    )


def _make_satellites(
    count: int = 36,
    planes: int = 1,
    isl_terminals: int = 4,
    ground_terminals: int = 1,
    ground_terminal_type: str = "rf",
) -> list[SatelliteNode]:
    sats = []
    sats_per_plane = count // planes
    for p in range(planes):
        for s in range(sats_per_plane):
            elems = elements_from_params(
                altitude_km=550.0,
                inclination_deg=53.0,
                raan_deg=p * 15.0,
                true_anomaly_deg=s * (360.0 / sats_per_plane),
            )
            sats.append(
                SatelliteNode(
                    plane=p,
                    slot=s,
                    elements=elems,
                    isl_terminal_count=isl_terminals,
                    ground_terminal_count=ground_terminals,
                    isl_terminals=(
                        IslTerminal(
                            type="optical",
                            count=isl_terminals,
                            max_range_km=5000.0,
                            bandwidth_mbps=100000.0,
                            max_tracking_rate_deg_s=5.0,
                        ),
                    )
                    if isl_terminals > 0
                    else (),
                    ground_terminals=(
                        GroundTerminal(
                            type=ground_terminal_type,
                            count=ground_terminals,
                            bandwidth_mbps=1000.0,
                        ),
                    )
                    if ground_terminals > 0
                    else (),
                )
            )
    return sats


def _make_constellation(
    *,
    planes: int = 1,
    sats_per_plane: int = 36,
    inclination_deg: float = 53.0,
    altitude_km: float = 550.0,
) -> ParametricConstellation:
    return ParametricConstellation(
        mode="parametric",
        name="test-constellation",
        orbit=OrbitParams(
            altitude_km=altitude_km,
            inclination_deg=inclination_deg,
            pattern="walker-delta",
        ),
        planes=PlaneParams(
            count=planes,
            raan_spacing_deg=360.0 / max(planes, 1),
            sats_per_plane=sats_per_plane,
            phase_offset_deg=0.0,
        ),
        default_terminals=TerminalConfig(
            isl=[
                IslTerminal(
                    type="optical",
                    count=4,
                    max_range_km=5000.0,
                    bandwidth_mbps=100000.0,
                    max_tracking_rate_deg_s=5.0,
                ),
            ],
            ground=[
                GroundTerminal(type="rf", count=1, bandwidth_mbps=1000.0),
            ],
        ),
    )


def _make_resolved_stack(
    segment_routing: bool = False,
) -> ResolvedStack:
    """Build a resolved stack. Uses real resolve_stack for SR to get real SRGB values."""
    if segment_routing:
        return resolve_stack("isis", ["traffic-engineering", "sr"])
    return resolve_stack("isis", ["traffic-engineering"])


def _make_tle_constellation() -> TLEConstellation:
    return TLEConstellation(
        mode="tle",
        name="sample-tle",
        tle_file="tests/fixtures/tles/sample.tle",
        default_terminals=TerminalConfig(
            isl=[
                IslTerminal(
                    type="optical",
                    count=2,
                    max_range_km=5000.0,
                    bandwidth_mbps=100000.0,
                    max_tracking_rate_deg_s=5.0,
                ),
            ],
            ground=[GroundTerminal(type="rf", count=1, bandwidth_mbps=1000.0)],
        ),
    )


def _make_sgp4_session(start_time: str = "2021-03-16T12:15:00+00:00") -> SessionConfig:
    session = _make_session()
    return session.model_copy(
        update={
            "orbit": OrbitConfig(propagator="sgp4-tle", tle_max_age_days=2.0),
            "time": TimeConfig(start_time=start_time, step_seconds=1),
        }
    )


def _make_tle_satellites() -> list[SatelliteNode]:
    return [
        SatelliteNode(
            plane=0,
            slot=0,
            elements=elements_from_params(420.0, 51.6, 21.5, 21.8),
            isl_terminal_count=2,
            ground_terminal_count=1,
            tle_line_1=ISS_TLE_LINE_1,
            tle_line_2=ISS_TLE_LINE_2,
            norad_id=25544,
        )
    ]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestValidSession:
    def test_valid_session_passes(self):
        """A fully valid session produces zero errors and zero warnings."""
        session = _make_session()
        gs = _make_gs_file()
        sats = _make_satellites()
        constellation = _make_constellation()
        stack = _make_resolved_stack()

        results = validate_session_readiness(
            session,
            constellation,
            sats,
            gs,
            stack,
            available_node_count=2,
        )

        errors = [r for r in results if r.level == "error"]
        assert errors == [], f"Unexpected errors: {errors}"
        # Warnings are OK but no errors
        # (W001 won't fire because our station has explicit terminals)


class TestPhysicsSourceValidation:
    def test_sgp4_requires_tle_constellation_source(self):
        results = validate_session_readiness(
            _make_sgp4_session(),
            _make_constellation(),
            _make_satellites(count=1),
            _make_gs_file(),
            _make_resolved_stack(),
        )

        assert any(r.level == "error" and r.code == "E008" for r in results)
        assert any("require a TLE-backed constellation" in r.message for r in results)

    def test_tle_constellation_requires_sgp4_propagator(self):
        results = validate_session_readiness(
            _make_session(),
            _make_tle_constellation(),
            _make_tle_satellites(),
            _make_gs_file(),
            _make_resolved_stack(),
        )

        assert any(r.level == "error" and r.code == "E008" for r in results)
        assert any("must not be approximated" in r.message for r in results)

    def test_sgp4_rejects_stale_tle(self):
        results = validate_session_readiness(
            _make_sgp4_session(start_time="2026-05-10T00:00:00+00:00"),
            _make_tle_constellation(),
            _make_tle_satellites(),
            _make_gs_file(),
            _make_resolved_stack(),
        )

        assert any(r.level == "error" and r.code == "E008" for r in results)
        assert any("TLE age exceeds" in r.message for r in results)

    def test_sgp4_accepts_fresh_tle(self):
        results = validate_session_readiness(
            _make_sgp4_session(),
            _make_tle_constellation(),
            _make_tle_satellites(),
            _make_gs_file(),
            _make_resolved_stack(),
        )

        errors = [r for r in results if r.level == "error"]
        assert errors == []


# ---------------------------------------------------------------------------
# E003: Empty terminal lists
# ---------------------------------------------------------------------------


class TestE003:
    def test_satellite_no_ground_terminals(self):
        """Satellite type with empty ground_terminals when GS exist = error."""
        session = _make_session()
        gs = _make_gs_file()
        sats = _make_satellites(ground_terminals=0)
        constellation = _make_constellation()
        stack = _make_resolved_stack()

        results = validate_session_readiness(
            session,
            constellation,
            sats,
            gs,
            stack,
        )

        errors = [r for r in results if r.level == "error" and r.code == "E003"]
        assert len(errors) == 1
        assert "0 ground terminals" in errors[0].message

    def test_satellite_no_isl_terminals_with_igp(self):
        """Satellite with 0 ISL terminals + IGP routing = error."""
        session = _make_session(protocol="isis")
        gs = _make_gs_file()
        sats = _make_satellites(isl_terminals=0)
        constellation = _make_constellation()
        stack = _make_resolved_stack()

        results = validate_session_readiness(
            session,
            constellation,
            sats,
            gs,
            stack,
        )

        errors = [r for r in results if r.level == "error" and r.code == "E003"]
        assert len(errors) == 1
        assert "0 ISL terminals" in errors[0].message

    def test_valid_terminals_no_e003(self):
        """Valid terminal counts produce no E003 errors."""
        session = _make_session()
        gs = _make_gs_file()
        sats = _make_satellites(isl_terminals=4, ground_terminals=1)
        constellation = _make_constellation()
        stack = _make_resolved_stack()

        results = validate_session_readiness(
            session,
            constellation,
            sats,
            gs,
            stack,
        )

        e003 = [r for r in results if r.code == "E003"]
        assert len(e003) == 0


# ---------------------------------------------------------------------------
# E004: SRGB overflow
# ---------------------------------------------------------------------------


class TestE004:
    def test_srgb_overflow(self):
        """Constellation too large for SRGB = error."""
        session = _make_session()
        gs = _make_gs_file()
        # 80 planes * 100 = 8000, which exceeds gs_sid_offset of 7900
        sats = _make_satellites(count=80 * 22, planes=80, isl_terminals=4)
        constellation = _make_constellation(planes=80, sats_per_plane=22)
        stack = _make_resolved_stack(segment_routing=True)

        results = validate_session_readiness(
            session,
            constellation,
            sats,
            gs,
            stack,
        )

        errors = [r for r in results if r.level == "error" and r.code == "E004"]
        assert len(errors) == 1
        assert "SRGB" in errors[0].message

    def test_no_sr_no_e004(self):
        """Without segment routing, E004 never fires."""
        session = _make_session()
        gs = _make_gs_file()
        sats = _make_satellites(count=80 * 22, planes=80)
        constellation = _make_constellation(planes=80, sats_per_plane=22)
        stack = _make_resolved_stack(segment_routing=False)

        results = validate_session_readiness(
            session,
            constellation,
            sats,
            gs,
            stack,
        )

        e004 = [r for r in results if r.code == "E004"]
        assert len(e004) == 0

    def test_small_constellation_sr_ok(self):
        """Small constellation with SR should not trigger E004."""
        session = _make_session()
        gs = _make_gs_file()
        sats = _make_satellites(count=36, planes=1)
        constellation = _make_constellation(planes=1, sats_per_plane=36)
        stack = _make_resolved_stack(segment_routing=True)

        results = validate_session_readiness(
            session,
            constellation,
            sats,
            gs,
            stack,
        )

        e004 = [r for r in results if r.code == "E004"]
        assert len(e004) == 0


# ---------------------------------------------------------------------------
# E005: Invalid scheduling_policy
# ---------------------------------------------------------------------------


class TestE005:
    def test_invalid_scheduling_policy(self):
        """Typo'd scheduling_policy = error."""
        session = _make_session()
        gs = _make_gs_file(default_scheduling_policy="best-signal")
        sats = _make_satellites()
        constellation = _make_constellation()
        stack = _make_resolved_stack()

        results = validate_session_readiness(
            session,
            constellation,
            sats,
            gs,
            stack,
        )

        errors = [r for r in results if r.level == "error" and r.code == "E005"]
        assert len(errors) == 1
        assert "best-signal" in errors[0].message

    def test_invalid_station_scheduling_policy(self):
        """Per-station invalid scheduling_policy = error."""
        station = GroundStationConfig(
            name="bad-policy",
            lat_deg=34.0,
            lon_deg=-118.0,
            scheduling_policy="round-robin",
        )
        gs = _make_gs_file(stations=[station])
        session = _make_session()
        sats = _make_satellites()
        constellation = _make_constellation()
        stack = _make_resolved_stack()

        results = validate_session_readiness(
            session,
            constellation,
            sats,
            gs,
            stack,
        )

        errors = [r for r in results if r.level == "error" and r.code == "E005"]
        assert len(errors) == 1
        assert "round-robin" in errors[0].message

    def test_valid_scheduling_policies_pass(self):
        """All valid scheduling policies produce no E005."""
        for policy in VALID_SCHEDULING_POLICIES:
            gs = _make_gs_file(default_scheduling_policy=policy)
            session = _make_session()
            sats = _make_satellites()
            constellation = _make_constellation()
            stack = _make_resolved_stack()

            results = validate_session_readiness(
                session,
                constellation,
                sats,
                gs,
                stack,
            )

            e005 = [r for r in results if r.code == "E005"]
            assert len(e005) == 0, f"Policy '{policy}' triggered E005: {e005}"


# ---------------------------------------------------------------------------
# E009: Future-dwell policy requires lookahead horizon
# ---------------------------------------------------------------------------


class TestE009:
    def test_longest_remaining_pass_default_policy_requires_lookahead_horizon(self):
        session = _make_session()
        gs = _make_gs_file(default_scheduling_policy="longest-remaining-pass")
        sats = _make_satellites()
        constellation = _make_constellation()
        stack = _make_resolved_stack()

        results = validate_session_readiness(session, constellation, sats, gs, stack)

        errors = [r for r in results if r.level == "error" and r.code == "E009"]
        assert len(errors) == 1
        assert "lookahead_horizon_ticks" in errors[0].message

    def test_longest_remaining_pass_station_override_requires_lookahead_horizon(self):
        session = _make_session()
        gs = _make_gs_file(
            stations=[
                GroundStationConfig(
                    name="dwell-policy",
                    lat_deg=34.0,
                    lon_deg=-118.0,
                    scheduling_policy="longest-remaining-pass",
                )
            ]
        )
        sats = _make_satellites()
        constellation = _make_constellation()
        stack = _make_resolved_stack()

        results = validate_session_readiness(session, constellation, sats, gs, stack)

        errors = [r for r in results if r.level == "error" and r.code == "E009"]
        assert len(errors) == 1
        assert "stations.dwell-policy.scheduling_policy" in errors[0].message

    def test_longest_remaining_pass_with_lookahead_horizon_passes(self):
        session = _make_session()
        session.scheduling.ground.lookahead_horizon_ticks = 600
        gs = _make_gs_file(default_scheduling_policy="longest-remaining-pass")
        sats = _make_satellites()
        constellation = _make_constellation()
        stack = _make_resolved_stack()

        results = validate_session_readiness(session, constellation, sats, gs, stack)

        e009 = [r for r in results if r.code == "E009"]
        assert e009 == []


# ---------------------------------------------------------------------------
# E010: MBB requires enough ground terminal capacity
# ---------------------------------------------------------------------------


class TestE010:
    def test_mbb_requires_capacity_for_steady_link_plus_reserve(self):
        session = _make_session()
        session.scheduling.ground.handover_mode = "mbb"
        session.scheduling.ground.mbb_overlap_ticks = 3
        session.scheduling.ground.mbb_reserve = 1
        gs = _make_gs_file(
            stations=[
                GroundStationConfig(
                    name="single-terminal",
                    lat_deg=34.0,
                    lon_deg=-118.0,
                    terminals=[
                        GroundTerminalDef(
                            type="rf",
                            count=1,
                            bandwidth_mbps=1000,
                            tracking_capacity=1,
                        )
                    ],
                )
            ]
        )
        sats = _make_satellites()
        constellation = _make_constellation()
        stack = _make_resolved_stack()

        results = validate_session_readiness(session, constellation, sats, gs, stack)

        errors = [r for r in results if r.level == "error" and r.code == "E010"]
        assert len(errors) == 1
        assert "capacity 1" in errors[0].message
        assert "requires capacity >= 2" in errors[0].message

    def test_mbb_capacity_uses_terminal_count_times_tracking_capacity(self):
        session = _make_session()
        session.scheduling.ground.handover_mode = "mbb"
        session.scheduling.ground.mbb_overlap_ticks = 3
        session.scheduling.ground.mbb_reserve = 1
        gs = _make_gs_file(
            stations=[
                GroundStationConfig(
                    name="two-terminals",
                    lat_deg=34.0,
                    lon_deg=-118.0,
                    terminals=[
                        GroundTerminalDef(
                            type="rf",
                            count=2,
                            bandwidth_mbps=1000,
                            tracking_capacity=1,
                        )
                    ],
                )
            ]
        )
        sats = _make_satellites()
        constellation = _make_constellation()
        stack = _make_resolved_stack()

        results = validate_session_readiness(session, constellation, sats, gs, stack)

        assert [r for r in results if r.code == "E010"] == []


# ---------------------------------------------------------------------------
# E011: Satellite / ground terminal type compatibility
# ---------------------------------------------------------------------------


class TestE011:
    def test_matching_ground_terminal_types_pass(self):
        session = _make_session()
        gs = _make_gs_file()
        sats = _make_satellites(ground_terminal_type="rf")
        constellation = _make_constellation()
        stack = _make_resolved_stack()

        results = validate_session_readiness(session, constellation, sats, gs, stack)

        assert [r for r in results if r.code == "E011"] == []

    def test_mismatched_ground_terminal_types_fail_before_deploy(self):
        session = _make_session()
        gs = _make_gs_file(
            stations=[
                GroundStationConfig(
                    name="optical-gs",
                    lat_deg=34.0,
                    lon_deg=-118.0,
                    terminals=[
                        GroundTerminalDef(
                            type="optical",
                            count=1,
                            bandwidth_mbps=1000,
                            tracking_capacity=1,
                        )
                    ],
                )
            ]
        )
        sats = _make_satellites(ground_terminal_type="rf")
        constellation = _make_constellation()
        stack = _make_resolved_stack()

        results = validate_session_readiness(session, constellation, sats, gs, stack)

        errors = [r for r in results if r.level == "error" and r.code == "E011"]
        assert len(errors) == 1
        assert "Ground terminal type mismatch" in errors[0].message
        assert "ground station uses 'optical'" in errors[0].message
        assert "satellite uses 'rf'" in errors[0].message


# ---------------------------------------------------------------------------
# E007: PlacementConfig coherence
# ---------------------------------------------------------------------------


class TestE007:
    def test_plane_group_without_planes_per_group(self):
        """planeGroupPerNode without planes_per_group = error."""
        session = _make_session(
            placement_policy="planeGroupPerNode",
            planes_per_group=None,
        )
        gs = _make_gs_file()
        sats = _make_satellites()
        constellation = _make_constellation()
        stack = _make_resolved_stack()

        results = validate_session_readiness(
            session,
            constellation,
            sats,
            gs,
            stack,
        )

        errors = [r for r in results if r.level == "error" and r.code == "E007"]
        assert len(errors) == 1
        assert "planes_per_group" in errors[0].message

    def test_plane_group_with_planes_per_group_ok(self):
        """planeGroupPerNode with planes_per_group set = no error."""
        session = _make_session(
            placement_policy="planeGroupPerNode",
            planes_per_group=4,
        )
        gs = _make_gs_file()
        sats = _make_satellites()
        constellation = _make_constellation()
        stack = _make_resolved_stack()

        results = validate_session_readiness(
            session,
            constellation,
            sats,
            gs,
            stack,
        )

        e007 = [r for r in results if r.code == "E007"]
        assert len(e007) == 0


# ---------------------------------------------------------------------------
# W001: Station has no terminals (using defaults)
# ---------------------------------------------------------------------------


class TestW001:
    def test_station_no_terminals(self):
        """Station with terminals=None = warning."""
        station = GroundStationConfig(
            name="no-terminals",
            lat_deg=34.0,
            lon_deg=-118.0,
            # terminals is None by default
        )
        gs = _make_gs_file(stations=[station])
        session = _make_session()
        sats = _make_satellites()
        constellation = _make_constellation()
        stack = _make_resolved_stack()

        results = validate_session_readiness(
            session,
            constellation,
            sats,
            gs,
            stack,
        )

        warnings = [r for r in results if r.level == "warning" and r.code == "W001"]
        assert len(warnings) == 1
        assert "no-terminals" in warnings[0].message

    def test_station_with_terminals_no_w001(self):
        """Station with explicit terminals = no W001."""
        gs = _make_gs_file()  # Default helper has explicit terminals
        session = _make_session()
        sats = _make_satellites()
        constellation = _make_constellation()
        stack = _make_resolved_stack()

        results = validate_session_readiness(
            session,
            constellation,
            sats,
            gs,
            stack,
        )

        w001 = [r for r in results if r.code == "W001"]
        assert len(w001) == 0


# ---------------------------------------------------------------------------
# W003: Station outside visibility band
# ---------------------------------------------------------------------------


class TestW003:
    def test_station_outside_visibility(self):
        """McMurdo at -77.8 deg with 53 deg inclination = warning."""
        station = GroundStationConfig(
            name="McMurdo",
            lat_deg=-77.8,
            lon_deg=166.7,
            terminals=[
                GroundTerminalDef(
                    type="rf",
                    count=1,
                    bandwidth_mbps=1000,
                    tracking_capacity=1,
                ),
            ],
        )
        gs = _make_gs_file(stations=[station])
        session = _make_session()
        sats = _make_satellites()
        constellation = _make_constellation(inclination_deg=53.0)
        stack = _make_resolved_stack()

        results = validate_session_readiness(
            session,
            constellation,
            sats,
            gs,
            stack,
        )

        warnings = [r for r in results if r.level == "warning" and r.code == "W003"]
        assert len(warnings) == 1
        assert "McMurdo" in warnings[0].message

    def test_station_inside_visibility_no_w003(self):
        """Los Angeles at 34 deg with 53 deg inclination = no warning."""
        gs = _make_gs_file()  # Default is at lat 34, inclination 53 — well within
        session = _make_session()
        sats = _make_satellites()
        constellation = _make_constellation(inclination_deg=53.0)
        stack = _make_resolved_stack()

        results = validate_session_readiness(
            session,
            constellation,
            sats,
            gs,
            stack,
        )

        w003 = [r for r in results if r.code == "W003"]
        assert len(w003) == 0

    def test_high_inclination_polar_station_ok(self):
        """Polar station with polar orbit (97 deg inclination) = no warning."""
        station = GroundStationConfig(
            name="Svalbard",
            lat_deg=78.2,
            lon_deg=15.4,
            terminals=[
                GroundTerminalDef(
                    type="rf",
                    count=1,
                    bandwidth_mbps=1000,
                    tracking_capacity=1,
                ),
            ],
        )
        gs = _make_gs_file(stations=[station])
        session = _make_session()
        sats = _make_satellites()
        constellation = _make_constellation(inclination_deg=97.0)
        stack = _make_resolved_stack()

        results = validate_session_readiness(
            session,
            constellation,
            sats,
            gs,
            stack,
        )

        w003 = [r for r in results if r.code == "W003"]
        assert len(w003) == 0


# ---------------------------------------------------------------------------
# W004: Cluster capacity
# ---------------------------------------------------------------------------


class TestW004:
    def test_cluster_capacity(self):
        """2000 satellites on 4 nodes = warning (2000 + gs > 4*200)."""
        session = _make_session()
        gs = _make_gs_file()
        sats = _make_satellites(count=2000, planes=40)
        constellation = _make_constellation(planes=40, sats_per_plane=50)
        stack = _make_resolved_stack()

        results = validate_session_readiness(
            session,
            constellation,
            sats,
            gs,
            stack,
            available_node_count=4,
        )

        warnings = [r for r in results if r.level == "warning" and r.code == "W004"]
        assert len(warnings) == 1

    def test_small_constellation_capacity_ok(self):
        """36 sats on 1 node = no warning."""
        session = _make_session()
        gs = _make_gs_file()
        sats = _make_satellites(count=36)
        constellation = _make_constellation()
        stack = _make_resolved_stack()

        results = validate_session_readiness(
            session,
            constellation,
            sats,
            gs,
            stack,
            available_node_count=1,
        )

        w004 = [r for r in results if r.code == "W004"]
        assert len(w004) == 0


# ---------------------------------------------------------------------------
# W005: OME compute budget
# ---------------------------------------------------------------------------


class TestW005:
    def test_ome_budget(self):
        """1600 sats at 1s step = warning."""
        session = _make_session(step_seconds=1)
        gs = _make_gs_file()
        sats = _make_satellites(count=1600, planes=32)
        constellation = _make_constellation(planes=32, sats_per_plane=50)
        stack = _make_resolved_stack()

        results = validate_session_readiness(
            session,
            constellation,
            sats,
            gs,
            stack,
        )

        warnings = [r for r in results if r.level == "warning" and r.code == "W005"]
        assert len(warnings) == 1

    def test_large_constellation_with_2s_step_ok(self):
        """1600 sats at 2s step = no warning."""
        session = _make_session(step_seconds=2)
        gs = _make_gs_file()
        sats = _make_satellites(count=1600, planes=32)
        constellation = _make_constellation(planes=32, sats_per_plane=50)
        stack = _make_resolved_stack()

        results = validate_session_readiness(
            session,
            constellation,
            sats,
            gs,
            stack,
        )

        w005 = [r for r in results if r.code == "W005"]
        assert len(w005) == 0


# ---------------------------------------------------------------------------
# W006: BFD interval below step granularity
# ---------------------------------------------------------------------------


class TestW006:
    def test_bfd_below_step(self):
        """BFD 300ms rx with 1s step = no warning (300 < 1000 only matters if bfd enabled)."""
        session = _make_session(bfd=True, bfd_rx_interval=300, step_seconds=1)
        gs = _make_gs_file()
        sats = _make_satellites()
        constellation = _make_constellation()
        stack = _make_resolved_stack()

        results = validate_session_readiness(
            session,
            constellation,
            sats,
            gs,
            stack,
        )

        warnings = [r for r in results if r.level == "warning" and r.code == "W006"]
        assert len(warnings) == 1

    def test_bfd_disabled_no_w006(self):
        """BFD disabled = no W006 regardless of rx_interval."""
        session = _make_session(bfd=False, bfd_rx_interval=100)
        gs = _make_gs_file()
        sats = _make_satellites()
        constellation = _make_constellation()
        stack = _make_resolved_stack()

        results = validate_session_readiness(
            session,
            constellation,
            sats,
            gs,
            stack,
        )

        w006 = [r for r in results if r.code == "W006"]
        assert len(w006) == 0

    def test_bfd_above_step_no_w006(self):
        """BFD 2000ms rx with 1s step = no warning."""
        session = _make_session(bfd=True, bfd_rx_interval=2000, step_seconds=1)
        gs = _make_gs_file()
        sats = _make_satellites()
        constellation = _make_constellation()
        stack = _make_resolved_stack()

        results = validate_session_readiness(
            session,
            constellation,
            sats,
            gs,
            stack,
        )

        w006 = [r for r in results if r.code == "W006"]
        assert len(w006) == 0


# ---------------------------------------------------------------------------
# W007: Bandwidth capacity imbalance
# ---------------------------------------------------------------------------


class TestW007:
    def test_bandwidth_imbalance(self):
        """Large ISL aggregate vs tiny GS = warning."""
        session = _make_session()
        # Tiny GS bandwidth
        station = GroundStationConfig(
            name="tiny-gs",
            lat_deg=34.0,
            lon_deg=-118.0,
            terminals=[
                GroundTerminalDef(
                    type="rf",
                    count=1,
                    bandwidth_mbps=10.0,
                    tracking_capacity=1,
                ),
            ],
        )
        gs = _make_gs_file(stations=[station])
        # 1000 sats * 4 ISL terminals * 100000 Mbps = huge ISL bandwidth
        sats = _make_satellites(count=1000, planes=20)
        constellation = _make_constellation(planes=20, sats_per_plane=50)
        stack = _make_resolved_stack()

        results = validate_session_readiness(
            session,
            constellation,
            sats,
            gs,
            stack,
            available_node_count=10,
        )

        warnings = [r for r in results if r.level == "warning" and r.code == "W007"]
        assert len(warnings) == 1

    def test_balanced_bandwidth_no_w007(self):
        """Balanced ISL and GS bandwidth = no warning."""
        session = _make_session()
        # Many GS with high bandwidth
        stations = [
            GroundStationConfig(
                name=f"gs-{i}",
                lat_deg=34.0 + i,
                lon_deg=-118.0 + i,
                terminals=[
                    GroundTerminalDef(
                        type="rf",
                        count=8,
                        bandwidth_mbps=100000.0,
                        tracking_capacity=8,
                    ),
                ],
            )
            for i in range(20)
        ]
        gs = _make_gs_file(stations=stations)
        sats = _make_satellites(count=36)
        constellation = _make_constellation()
        stack = _make_resolved_stack()

        results = validate_session_readiness(
            session,
            constellation,
            sats,
            gs,
            stack,
            available_node_count=2,
        )

        w007 = [r for r in results if r.code == "W007"]
        assert len(w007) == 0


# ---------------------------------------------------------------------------
# W008: Latency/timeout coherence
# ---------------------------------------------------------------------------


class TestW008:
    def test_latency_timer_mismatch(self):
        """BFD 300ms on high-altitude orbit = warning when round-trip delay > 300ms."""
        # At 36000 km (GEO): delay = 36000/300*2 = 240ms. Not enough.
        # Use altitude where delay > bfd_rx_interval.
        # At 55000 km: delay = 55000/300*2 = 366ms > 300ms
        session = _make_session(bfd=True, bfd_rx_interval=300)
        gs = _make_gs_file()
        sats = _make_satellites()
        # Very high altitude constellation
        constellation = _make_constellation(altitude_km=55000.0)
        stack = _make_resolved_stack()

        results = validate_session_readiness(
            session,
            constellation,
            sats,
            gs,
            stack,
        )

        warnings = [r for r in results if r.level == "warning" and r.code == "W008"]
        assert len(warnings) == 1

    def test_bfd_disabled_no_w008(self):
        """BFD disabled = no W008."""
        session = _make_session(bfd=False)
        gs = _make_gs_file()
        sats = _make_satellites()
        constellation = _make_constellation(altitude_km=55000.0)
        stack = _make_resolved_stack()

        results = validate_session_readiness(
            session,
            constellation,
            sats,
            gs,
            stack,
        )

        w008 = [r for r in results if r.code == "W008"]
        assert len(w008) == 0

    def test_leo_orbit_bfd_ok(self):
        """BFD 300ms on 550km LEO orbit = no warning (delay ~3.7ms)."""
        session = _make_session(bfd=True, bfd_rx_interval=300)
        gs = _make_gs_file()
        sats = _make_satellites()
        constellation = _make_constellation(altitude_km=550.0)
        stack = _make_resolved_stack()

        results = validate_session_readiness(
            session,
            constellation,
            sats,
            gs,
            stack,
        )

        w008 = [r for r in results if r.code == "W008"]
        assert len(w008) == 0


class TestValidationReport:
    def test_valid_report_is_dispatchable_and_contains_effective_config(self):
        session = _make_session()
        report = build_validation_report(session, [])

        assert report.status == "valid"
        assert report.dispatchable is True
        assert report.normalized_schema_version == 2
        assert report.errors == ()
        assert report.effective_config["orbit"]["propagator"] == "keplerian-circular"

    def test_invalid_report_separates_errors_and_warnings(self):
        session = _make_session()
        error = ValidationResult(
            level="error",
            code="E999",
            message="bad config",
            remediation="fix it",
            field_path="orbit.propagator",
        )
        warning = ValidationResult(
            level="warning",
            code="W999",
            message="risky config",
        )

        report = build_validation_report(session, [warning, error])

        assert report.status == "invalid"
        assert report.dispatchable is False
        assert report.errors == (error,)
        assert report.warnings == (warning,)
        assert report.errors[0].field_path == "orbit.propagator"


# ---------------------------------------------------------------------------
# Regression gate: all real session files must pass (zero errors)
# ---------------------------------------------------------------------------


class TestExistingSessions:
    """Load every YAML in configs/sessions/, validate, assert zero errors."""

    @pytest.fixture(params=sorted((CONFIGS_DIR / "sessions").glob("*.yaml")), ids=lambda p: p.stem)
    def session_path(self, request):
        return request.param

    def test_existing_sessions_pass(self, session_path):
        """Real session YAML files must produce zero validation errors."""
        raw = yaml.safe_load(session_path.read_text())
        session = SessionConfig.model_validate(raw)

        constellation = load_constellation(session.constellation)
        gs_file = load_ground_stations(session.ground_stations)
        satellites = expand_constellation(constellation)

        protocol = session.routing.protocol or "isis"
        extensions = session.routing.extensions
        resolved = resolve_stack(protocol, extensions)

        results = validate_session_readiness(
            session,
            constellation,
            satellites,
            gs_file,
            resolved,
            available_node_count=4,  # Assume 4-node cluster for capacity check
        )

        errors = [r for r in results if r.level == "error"]
        assert errors == [], f"Session {session_path.name} has validation errors: " + "; ".join(
            f"[{e.code}] {e.message}" for e in errors
        )
