# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Session pre-deployment validation — pure functions, no I/O.

Takes fully-resolved models and returns a list of ValidationResult.
No K8s, no NATS, no file system access, no imports from services/.
"""

from __future__ import annotations

from nodalarc.ground_terminals import (
    ground_terminal_type,
    station_ground_terminal_capacity,
    station_ground_terminal_type,
    terminal_collection_missing_physics,
)
from nodalarc.models.constellation import (
    ConstellationConfig,
    ParametricConstellation,
    TLEConstellation,
)
from nodalarc.models.events import ValidationReport, ValidationResult
from nodalarc.models.ground_station import GroundStationFile
from nodalarc.models.session import SessionConfig, resolve_session_epoch
from nodalarc.stack_resolver import ResolvedStack
from nodalarc.tle import tle_age_days

# Canonical list of valid scheduling policies. Checked by E005.
VALID_SCHEDULING_POLICIES = frozenset(
    {
        "highest-elevation",
        "lowest-elevation",
        "longest-remaining-pass",
    }
)


def validate_session_readiness(
    session: SessionConfig,
    constellation: ConstellationConfig,
    satellites: list,
    ground_stations: GroundStationFile,
    resolved_stack: ResolvedStack,
    available_node_count: int = 1,
) -> list[ValidationResult]:
    """Validate a session before deployment.

    Args:
        session: Parsed SessionConfig.
        constellation: Parsed ConstellationConfig (Parametric, Explicit, or TLE).
        satellites: Expanded SatelliteNode list from expand_constellation().
        ground_stations: Parsed GroundStationFile.
        resolved_stack: Output of resolve_stack().
        available_node_count: Number of K8s nodes available for pod placement.

    Returns:
        List of ValidationResult. Errors block deployment; warnings are logged.
    """
    results: list[ValidationResult] = []

    results.extend(_check_e003(satellites, ground_stations, session))
    results.extend(_check_e004(satellites, resolved_stack))
    results.extend(_check_e005(ground_stations))
    results.extend(_check_e007(session))
    results.extend(_check_e008(session, constellation, satellites))
    results.extend(_check_e009(session, ground_stations))
    results.extend(_check_e010(session, ground_stations))
    results.extend(_check_e011(satellites, ground_stations))
    results.extend(_check_e020(session))
    results.extend(_check_e021(session, satellites, ground_stations))

    results.extend(_check_w001(ground_stations))
    results.extend(_check_w002(ground_stations))
    results.extend(_check_w003(constellation, ground_stations))
    results.extend(_check_w004(satellites, ground_stations, available_node_count))
    results.extend(_check_w005(satellites, session))
    results.extend(_check_w006(session))
    results.extend(_check_w007(satellites, ground_stations, constellation))
    results.extend(_check_w008(session, constellation))
    results.extend(_check_w009(session))

    return results


def build_validation_report(
    session: SessionConfig,
    results: list[ValidationResult],
) -> ValidationReport:
    """Build the user-facing validation report from readiness results."""
    errors = tuple(result for result in results if result.level == "error")
    warnings = tuple(result for result in results if result.level == "warning")
    return ValidationReport(
        status="invalid" if errors else "valid",
        normalized_schema_version=session.simulation.schema_version,
        effective_config=session.model_dump(mode="json"),
        errors=errors,
        warnings=warnings,
        dispatchable=not errors,
    )


# ---------------------------------------------------------------------------
# Error checks (block deployment)
# ---------------------------------------------------------------------------


def _check_e003(
    satellites: list,
    ground_stations: GroundStationFile,
    session: SessionConfig,
) -> list[ValidationResult]:
    """E003: Empty terminal lists that would prevent link formation."""
    results: list[ValidationResult] = []

    has_gs = len(ground_stations.stations) > 0

    # Check: satellite has 0 ground terminals but ground stations exist
    if has_gs:
        for sat in satellites:
            if sat.ground_terminal_count == 0:
                results.append(
                    ValidationResult(
                        level="error",
                        code="E003",
                        message=(
                            f"Satellite P{sat.plane:02d}S{sat.slot:02d} has 0 ground terminals "
                            f"but {len(ground_stations.stations)} ground stations are defined. "
                            f"No ground links can form."
                        ),
                        remediation="Add ground terminals to the satellite type definition.",
                    )
                )
                break  # One error is enough — all sats likely share the same type

    # Check: satellite has 0 ISL terminals but routing uses IGP (isis/ospf)
    protocol = session.routing.protocol
    if protocol in ("isis", "ospf"):
        for sat in satellites:
            if sat.isl_terminal_count == 0:
                results.append(
                    ValidationResult(
                        level="error",
                        code="E003",
                        message=(
                            f"Satellite P{sat.plane:02d}S{sat.slot:02d} has 0 ISL terminals "
                            f"but routing protocol is '{protocol}'. No ISL adjacencies can form."
                        ),
                        remediation="Add ISL terminals to the satellite type definition.",
                    )
                )
                break  # One error per category

    return results


def _check_e004(
    satellites: list,
    resolved_stack: ResolvedStack,
) -> list[ValidationResult]:
    """E004: SRGB overflow — constellation too large for SID index space."""
    results: list[ValidationResult] = []

    if not resolved_stack.segment_routing:
        return results

    tv = resolved_stack.template_variables
    gs_sid_offset = tv.get("gs_sid_offset")
    if gs_sid_offset is None:
        return results  # No SR variables to check

    # SID scheme from stack_resolver.py:validate_constellation_constraints
    # Satellite SID = plane * 100 + slot + 1
    max_plane = max((s.plane for s in satellites), default=0)
    max_slot = max((s.slot for s in satellites), default=0)
    max_sat_sid = max_plane * 100 + max_slot + 1

    if max_sat_sid >= gs_sid_offset:
        results.append(
            ValidationResult(
                level="error",
                code="E004",
                message=(
                    f"Satellite SID range (max {max_sat_sid}) overlaps GS SID offset "
                    f"({gs_sid_offset}). Constellation is too large for the SRGB."
                ),
                remediation="Increase SRGB range or reduce constellation size.",
            )
        )

    return results


def _check_e005(ground_stations: GroundStationFile) -> list[ValidationResult]:
    """E005: Invalid scheduling_policy values."""
    results: list[ValidationResult] = []

    if ground_stations.default_scheduling_policy not in VALID_SCHEDULING_POLICIES:
        results.append(
            ValidationResult(
                level="error",
                code="E005",
                message=(
                    f"Invalid default_scheduling_policy: "
                    f"'{ground_stations.default_scheduling_policy}'. "
                    f"Valid values: {', '.join(sorted(VALID_SCHEDULING_POLICIES))}"
                ),
                remediation="Fix the scheduling_policy in the ground station file.",
            )
        )

    for station in ground_stations.stations:
        if (
            station.scheduling_policy is not None
            and station.scheduling_policy not in VALID_SCHEDULING_POLICIES
        ):
            results.append(
                ValidationResult(
                    level="error",
                    code="E005",
                    message=(
                        f"Station '{station.name}' has invalid scheduling_policy: "
                        f"'{station.scheduling_policy}'. "
                        f"Valid values: {', '.join(sorted(VALID_SCHEDULING_POLICIES))}"
                    ),
                    remediation=(f"Fix scheduling_policy for station '{station.name}'."),
                )
            )

    return results


def _ground_policies_requiring_lookahead(ground_stations: GroundStationFile) -> list[str]:
    """Return station labels using future-dwell policies."""
    labels: list[str] = []
    if ground_stations.default_scheduling_policy == "longest-remaining-pass":
        labels.append("default_scheduling_policy")
    for station in ground_stations.stations:
        if station.scheduling_policy == "longest-remaining-pass":
            labels.append(f"stations.{station.name}.scheduling_policy")
    return labels


def _check_e007(session: SessionConfig) -> list[ValidationResult]:
    """E007: PlacementConfig coherence — planeGroupPerNode needs planes_per_group."""
    results: list[ValidationResult] = []

    if (
        session.placement.policy == "planeGroupPerNode"
        and session.placement.planes_per_group is None
    ):
        results.append(
            ValidationResult(
                level="error",
                code="E007",
                message=(
                    "Placement policy 'planeGroupPerNode' requires planes_per_group "
                    "to be set, but it is None."
                ),
                remediation="Set placement.planes_per_group in the session YAML.",
            )
        )

    return results


def _check_e008(
    session: SessionConfig,
    constellation: ConstellationConfig,
    satellites: list,
) -> list[ValidationResult]:
    """E008: Orbit propagator and constellation source must be coherent."""
    results: list[ValidationResult] = []
    is_tle_constellation = isinstance(constellation, TLEConstellation)
    is_sgp4 = session.orbit.propagator == "sgp4-tle"

    if is_sgp4 and not is_tle_constellation:
        results.append(
            ValidationResult(
                level="error",
                code="E008",
                message=(
                    "orbit.propagator is 'sgp4-tle' but constellation.mode is not 'tle'. "
                    "SGP4 sessions require a TLE-backed constellation source."
                ),
                remediation="Use a constellation with mode: tle, or choose a non-SGP4 propagator.",
            )
        )

    if is_tle_constellation and not is_sgp4:
        results.append(
            ValidationResult(
                level="error",
                code="E008",
                message=(
                    "constellation.mode is 'tle' but orbit.propagator is not 'sgp4-tle'. "
                    "TLE sources must not be approximated by a lower-fidelity propagator."
                ),
                remediation="Set orbit.propagator to 'sgp4-tle'.",
            )
        )

    if not is_sgp4:
        return results

    max_age_days = session.orbit.tle_max_age_days
    if max_age_days is None:
        results.append(
            ValidationResult(
                level="error",
                code="E008",
                message="orbit.tle_max_age_days is required for SGP4/TLE sessions.",
                remediation="Set orbit.tle_max_age_days to the accepted TLE age window.",
            )
        )
        return results

    missing_tle = [
        f"P{sat.plane:02d}S{sat.slot:02d}"
        for sat in satellites
        if getattr(sat, "tle_line_1", None) is None or getattr(sat, "tle_line_2", None) is None
    ]
    if missing_tle:
        results.append(
            ValidationResult(
                level="error",
                code="E008",
                message=(
                    "SGP4/TLE propagator selected but expanded satellites are missing "
                    f"TLE records: {', '.join(missing_tle[:5])}"
                ),
                remediation="Use a TLE constellation source for all SGP4 satellites.",
            )
        )
        return results

    sim_epoch_unix = resolve_session_epoch(session.time)
    stale: list[str] = []
    for sat in satellites:
        age_days = tle_age_days(sat.tle_line_1, sim_epoch_unix)
        if age_days > max_age_days:
            stale.append(
                f"P{sat.plane:02d}S{sat.slot:02d} "
                f"NORAD {getattr(sat, 'norad_id', 'unknown')} age {age_days:.2f}d"
            )

    if stale:
        results.append(
            ValidationResult(
                level="error",
                code="E008",
                message=(
                    f"TLE age exceeds orbit.tle_max_age_days={max_age_days:g}: "
                    f"{', '.join(stale[:5])}"
                ),
                remediation=(
                    "Use fresher TLEs, set time.start_time near the TLE epoch, or explicitly "
                    "increase orbit.tle_max_age_days if that error budget is acceptable."
                ),
            )
        )

    return results


def _check_e009(
    session: SessionConfig,
    ground_stations: GroundStationFile,
) -> list[ValidationResult]:
    """E009: Future-dwell ground policies require an explicit lookahead horizon."""
    labels = _ground_policies_requiring_lookahead(ground_stations)
    if not labels:
        return []
    if session.scheduling.ground.lookahead_horizon_ticks > 0:
        return []
    return [
        ValidationResult(
            level="error",
            code="E009",
            message=(
                "Ground scheduling policy 'longest-remaining-pass' requires "
                "scheduling.ground.lookahead_horizon_ticks > 0. Affected fields: "
                f"{', '.join(labels)}"
            ),
            remediation=(
                "Set scheduling.ground.lookahead_horizon_ticks to the dwell-prediction "
                "horizon, in OME ticks, or choose highest-elevation/lowest-elevation."
            ),
        )
    ]


def _check_e010(
    session: SessionConfig,
    ground_stations: GroundStationFile,
) -> list[ValidationResult]:
    """E010: MBB handover requires station capacity for steady + reserve links."""
    ground = session.scheduling.ground
    if ground.handover_mode != "mbb":
        return []

    required_capacity = ground.mbb_reserve + 1
    results: list[ValidationResult] = []
    for station in ground_stations.stations:
        capacity = station_ground_terminal_capacity(ground_stations, station)
        if capacity >= required_capacity:
            continue
        results.append(
            ValidationResult(
                level="error",
                code="E010",
                message=(
                    f"MBB handover requested, but station '{station.name}' has "
                    f"ground terminal capacity {capacity}. With mbb_reserve="
                    f"{ground.mbb_reserve}, MBB requires capacity >= {required_capacity} "
                    "so one steady link can exist while the reserved terminal is held "
                    "for make-before-break overlap."
                ),
                remediation=(
                    f"Increase terminal count/tracking_capacity for station '{station.name}', "
                    "lower mbb_reserve, or set scheduling.ground.handover_mode to 'bbm'."
                ),
                field_path="scheduling.ground.handover_mode",
            )
        )

    return results


def _check_e011(
    satellites: list,
    ground_stations: GroundStationFile,
) -> list[ValidationResult]:
    """E011: Satellite and ground-station ground terminal types must match."""
    if not ground_stations.stations:
        return []

    sat_types: dict[str, object] = {}
    for sat in satellites:
        if not getattr(sat, "ground_terminals", None):
            continue
        try:
            sat_type = ground_terminal_type(sat.ground_terminals)
        except ValueError as exc:
            return [
                ValidationResult(
                    level="error",
                    code="E011",
                    message=(
                        f"Satellite P{sat.plane:02d}S{sat.slot:02d} has invalid ground "
                        f"terminal definitions: {exc}"
                    ),
                    remediation=(
                        "Use a single ground terminal type per satellite until "
                        "terminal-block-aware allocation is implemented."
                    ),
                    field_path="constellation",
                )
            ]
        sat_types.setdefault(sat_type, sat)

    if not sat_types:
        return []

    station_types: dict[str, object] = {}
    for station in ground_stations.stations:
        try:
            gs_type = station_ground_terminal_type(ground_stations, station)
        except ValueError as exc:
            return [
                ValidationResult(
                    level="error",
                    code="E011",
                    message=(
                        f"Ground station '{station.name}' has invalid terminal definitions: {exc}"
                    ),
                    remediation=(
                        "Use a single ground terminal type per station until "
                        "terminal-block-aware allocation is implemented."
                    ),
                    field_path="ground_stations",
                )
            ]
        station_types.setdefault(gs_type, station)

    if set(station_types) == set(sat_types):
        return []

    mismatch = next(
        (
            (gs_type, station, sat_type, sat)
            for gs_type, station in station_types.items()
            for sat_type, sat in sat_types.items()
            if gs_type != sat_type
        )
    )
    gs_type, station, sat_type, sat = mismatch
    return [
        ValidationResult(
            level="error",
            code="E011",
            message=(
                f"Ground terminal type mismatch for gs-{station.name}<->"
                f"sat-P{sat.plane:02d}S{sat.slot:02d}: ground station uses "
                f"{gs_type!r}, satellite uses {sat_type!r}. Mixed terminal types "
                "require an explicit compatibility model."
            ),
            remediation=(
                "Select a ground station set whose terminal type matches the "
                "satellite ground terminals, or update the YAML hardware model."
            ),
            field_path="ground_stations",
        )
    ]


def _check_e020(session: SessionConfig) -> list[ValidationResult]:
    """E020: geometry_only requires explicit acknowledgement."""
    if session.simulation.fidelity != "geometry_only":
        return []
    if session.simulation.acknowledge_geometry_only:
        return []
    return [
        ValidationResult(
            level="error",
            code="E020",
            message=(
                "simulation.fidelity is 'geometry_only' but "
                "simulation.acknowledge_geometry_only is not true. "
                "Geometry-only mode omits ground terminal range/FoR/tracking "
                "physics and is not fidelity-grade."
            ),
            remediation=(
                "Either set simulation.fidelity to 'physical_v1' and declare "
                "ground terminal physics, or explicitly set "
                "simulation.acknowledge_geometry_only: true."
            ),
            field_path="simulation.acknowledge_geometry_only",
        )
    ]


def _check_e021(
    session: SessionConfig,
    satellites: list,
    ground_stations: GroundStationFile,
) -> list[ValidationResult]:
    """E021: physical_v1 requires ground terminal physics on both ends."""
    if session.simulation.fidelity == "geometry_only":
        return []
    if not ground_stations.stations:
        return []

    results: list[ValidationResult] = []
    for station in ground_stations.stations:
        terminals = station.terminals or ground_stations.default_terminals
        label = f"ground_stations.{station.name}.terminals"
        missing = terminal_collection_missing_physics(terminals, label=label)
        for error in missing:
            results.append(
                ValidationResult(
                    level="error",
                    code="E021",
                    message=(
                        f"physical_v1 requires ground terminal physics: {error}. "
                        "Ground links cannot be fidelity-grade without range, "
                        "field-of-regard, and tracking-rate limits."
                    ),
                    remediation=(
                        "Add max_range_km, field_of_regard_deg, "
                        "max_tracking_rate_deg_s, and boresight to the effective "
                        "ground terminal definition, or opt into geometry_only "
                        "with explicit acknowledgement."
                    ),
                    field_path="ground_stations",
                )
            )

    def _boresight_shape_without_target_body(boresight) -> tuple | None:
        if boresight is None:
            return None
        data = boresight.model_dump()
        data.pop("target_body", None)
        return tuple(sorted(data.items()))

    seen_sat_terminal_shapes: set[tuple] = set()
    for sat in satellites:
        terminals = tuple(getattr(sat, "ground_terminals", ()) or ())
        if not terminals and getattr(sat, "ground_terminal_count", 0) == 0:
            continue
        # Expanded constellations usually share the same satellite type. Avoid
        # emitting the same terminal-shape error hundreds of times. Satellite
        # ground terminals can be target-body-distinct while sharing the same
        # physical shape, so target_body is excluded from the dedupe key.
        shape = tuple(
            (
                getattr(t, "type", None),
                getattr(t, "count", None),
                getattr(t, "bandwidth_mbps", None),
                getattr(t, "max_range_km", None),
                getattr(t, "field_of_regard_deg", None),
                getattr(t, "max_tracking_rate_deg_s", None),
                _boresight_shape_without_target_body(getattr(t, "boresight", None)),
            )
            for t in terminals
        )
        if shape in seen_sat_terminal_shapes:
            continue
        seen_sat_terminal_shapes.add(shape)
        label = f"satellite P{sat.plane:02d}S{sat.slot:02d}.ground_terminals"
        missing = terminal_collection_missing_physics(terminals, label=label)
        for error in missing:
            results.append(
                ValidationResult(
                    level="error",
                    code="E021",
                    message=(
                        f"physical_v1 requires satellite ground terminal physics: {error}. "
                        "Ground links cannot be fidelity-grade without range, "
                        "field-of-regard, and tracking-rate limits on the satellite side."
                    ),
                    remediation=(
                        "Add max_range_km, field_of_regard_deg, "
                        "max_tracking_rate_deg_s, and boresight to the satellite "
                        "type ground_terminal definition, or opt into geometry_only "
                        "with explicit acknowledgement."
                    ),
                    field_path="satellite_type.ground_terminals",
                )
            )

    return results


# ---------------------------------------------------------------------------
# Warning checks (logged, deployment proceeds)
# ---------------------------------------------------------------------------


def _check_w001(ground_stations: GroundStationFile) -> list[ValidationResult]:
    """W001: Station has no terminals (using defaults)."""
    results: list[ValidationResult] = []

    for station in ground_stations.stations:
        if station.terminals is None:
            results.append(
                ValidationResult(
                    level="warning",
                    code="W001",
                    message=(
                        f"Station '{station.name}' has no terminals defined — "
                        f"using default_terminals from ground station file."
                    ),
                )
            )

    return results


def _check_w002(ground_stations: GroundStationFile) -> list[ValidationResult]:
    """W002: Station has placeholder terminal data.

    Flags when station.antennas > sum of terminal counts, indicating
    the terminal model doesn't yet reflect the actual hardware.
    """
    results: list[ValidationResult] = []

    for station in ground_stations.stations:
        if station.terminals is not None and station.antennas is not None:
            terminal_count = sum(t.count for t in station.terminals)
            if station.antennas > terminal_count:
                results.append(
                    ValidationResult(
                        level="warning",
                        code="W002",
                        message=(
                            f"Station '{station.name}' has {station.antennas} physical "
                            f"antennas but only {terminal_count} terminal(s) modeled — "
                            f"terminal data may be placeholder."
                        ),
                    )
                )

    return results


def _check_w003(
    constellation: ConstellationConfig,
    ground_stations: GroundStationFile,
) -> list[ValidationResult]:
    """W003: Ground station outside constellation visibility band.

    For parametric constellations, warn if abs(station.lat) > inclination + margin.
    Margin accounts for elevation angle geometry.
    """
    results: list[ValidationResult] = []

    if not isinstance(constellation, ParametricConstellation):
        return results

    inclination = constellation.orbit.inclination_deg

    for station in ground_stations.stations:
        min_elev = station.min_elevation_deg
        margin = max(5.0, 10.0 - min_elev / 10.0) if min_elev is not None else 10.0

        if abs(station.lat_deg) > inclination + margin:
            results.append(
                ValidationResult(
                    level="warning",
                    code="W003",
                    message=(
                        f"Station '{station.name}' at latitude {station.lat_deg:.1f} deg "
                        f"is outside the visibility band of a {inclination:.0f} deg "
                        f"inclination constellation (margin {margin:.0f} deg). "
                        f"This station will likely never see any satellites."
                    ),
                )
            )

    return results


def _check_w004(
    satellites: list,
    ground_stations: GroundStationFile,
    available_node_count: int,
) -> list[ValidationResult]:
    """W004: Constellation size may exceed cluster capacity."""
    results: list[ValidationResult] = []

    total_pods = len(satellites) + len(ground_stations.stations)
    pods_per_node = 200  # Reasonable ceiling per K8s node

    if total_pods > available_node_count * pods_per_node:
        results.append(
            ValidationResult(
                level="warning",
                code="W004",
                message=(
                    f"Total pods ({total_pods}) exceeds estimated cluster capacity "
                    f"({available_node_count} nodes x {pods_per_node} pods/node = "
                    f"{available_node_count * pods_per_node}). "
                    f"Deployment may fail or degrade node stability."
                ),
                remediation="Add more nodes or reduce constellation size.",
            )
        )

    return results


def _check_w005(
    satellites: list,
    session: SessionConfig,
) -> list[ValidationResult]:
    """W005: OME compute budget marginal for large constellations at 1s step."""
    results: list[ValidationResult] = []

    if len(satellites) > 1500 and session.time.step_seconds == 1:
        results.append(
            ValidationResult(
                level="warning",
                code="W005",
                message=(
                    f"Constellation has {len(satellites)} satellites with step_seconds=1. "
                    f"OME visibility computation may not complete within the step interval."
                ),
                remediation="Increase step_seconds to 2 or higher for large constellations.",
            )
        )

    return results


def _check_w006(session: SessionConfig) -> list[ValidationResult]:
    """W006: BFD interval below step granularity.

    Only checks if BFD is actually enabled.
    """
    results: list[ValidationResult] = []

    if not session.routing.bfd:
        return results

    step_ms = session.time.step_seconds * 1000
    if session.routing.bfd_rx_interval < step_ms:
        results.append(
            ValidationResult(
                level="warning",
                code="W006",
                message=(
                    f"BFD rx_interval ({session.routing.bfd_rx_interval}ms) is below "
                    f"the OME step granularity ({step_ms}ms). "
                    f"BFD cannot detect failures faster than the simulation step."
                ),
                remediation=(
                    f"Increase bfd_rx_interval to at least {step_ms}ms or decrease step_seconds."
                ),
            )
        )

    return results


def _check_w007(
    satellites: list,
    ground_stations: GroundStationFile,
    constellation: ConstellationConfig,
) -> list[ValidationResult]:
    """W007: Bandwidth capacity imbalance between ISL and GS aggregate."""
    results: list[ValidationResult] = []

    # Compute aggregate ISL bandwidth from constellation's default terminals
    # (we use satellite terminal counts as a proxy — exact bandwidth requires
    # resolving the full terminal model, but we can use default_terminals)
    if constellation.default_terminals is None:
        return results

    isl_bw_per_sat = sum(t.bandwidth_mbps * t.count for t in constellation.default_terminals.isl)
    total_isl_bw = isl_bw_per_sat * len(satellites)

    # Compute aggregate GS bandwidth
    total_gs_bw = 0.0
    for station in ground_stations.stations:
        terminals = station.terminals or ground_stations.default_terminals
        station_bw = sum(t.bandwidth_mbps * t.count for t in terminals)
        total_gs_bw += station_bw

    if total_gs_bw > 0 and total_isl_bw / total_gs_bw > 10:
        results.append(
            ValidationResult(
                level="warning",
                code="W007",
                message=(
                    f"Bandwidth imbalance: aggregate ISL bandwidth ({total_isl_bw:.0f} Mbps) "
                    f"is {total_isl_bw / total_gs_bw:.0f}x the aggregate GS bandwidth "
                    f"({total_gs_bw:.0f} Mbps). Ground segment may be a bottleneck."
                ),
                remediation="Add more ground stations or increase terminal bandwidth.",
            )
        )

    return results


def _check_w008(
    session: SessionConfig,
    constellation: ConstellationConfig,
) -> list[ValidationResult]:
    """W008: Latency/timeout coherence — BFD interval vs. orbital propagation delay.

    Only checks when BFD is enabled.
    """
    results: list[ValidationResult] = []

    if not session.routing.bfd:
        return results

    # Extract altitude for propagation delay estimate
    altitude_km: float | None = None
    if isinstance(constellation, ParametricConstellation):
        altitude_km = constellation.orbit.altitude_km

    if altitude_km is None:
        return results  # Can't compute without altitude

    # Rough minimum ISL latency: altitude / speed_of_light * 2 (round trip)
    # Speed of light ~= 300,000 km/s. For ISLs at same altitude, minimum
    # distance is roughly the satellite spacing, but the altitude gives
    # a reasonable floor for the propagation component.
    min_delay_ms = altitude_km / 300.0 * 2.0

    if session.routing.bfd_rx_interval < min_delay_ms:
        results.append(
            ValidationResult(
                level="warning",
                code="W008",
                message=(
                    f"BFD rx_interval ({session.routing.bfd_rx_interval}ms) is below "
                    f"the estimated minimum ISL round-trip delay "
                    f"({min_delay_ms:.1f}ms at {altitude_km:.0f}km altitude). "
                    f"BFD may flap due to propagation delay alone."
                ),
                remediation=(f"Increase bfd_rx_interval to at least {min_delay_ms:.0f}ms."),
            )
        )

    return results


def _check_w009(session: SessionConfig) -> list[ValidationResult]:
    """W009: geometry_only sessions are explicitly non-fidelity-grade."""
    if session.simulation.fidelity != "geometry_only":
        return []
    return [
        ValidationResult(
            level="warning",
            code="W009",
            message=(
                "simulation.fidelity is 'geometry_only'. Ground links use LOS "
                "and elevation only; range, field-of-regard, and tracking-rate "
                "limits are intentionally not enforced."
            ),
            remediation=(
                "Use simulation.fidelity: physical_v1 with terminal physics "
                "fields for fidelity-grade ground visibility."
            ),
            field_path="simulation.fidelity",
        )
    ]
