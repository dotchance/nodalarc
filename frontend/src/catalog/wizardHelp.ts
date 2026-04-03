// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Elastic License 2.0 (ELv2). See LICENSE file.
/** Plain-English help callouts for wizard advanced mode parameters.
 *
 * Text from PRD v51 R-WIZ-004 "Advanced mode parameters and required
 * help callouts."  This file is pure data — no logic.
 */

export const CONSTELLATION_HELP: Record<string, string> = {
  altitude_km:
    "Orbital altitude above Earth's surface. Lower orbits (300\u2013600 km) have shorter " +
    "propagation delay (~3\u20137 ms one-way) and more frequent ground station passes but " +
    "require more satellites for continuous coverage. Higher orbits (1200\u20132000 km) have " +
    "longer delay but better coverage per satellite.",

  inclination_deg:
    "Angle between the orbital plane and Earth's equator. 0\u00b0 = equatorial (covers only " +
    "equatorial latitudes). 53\u00b0 = covers most populated areas but not poles. 86\u00b0+ = " +
    "polar coverage, produces counter-rotating planes and the polar seam ISL dropout " +
    "phenomenon. Iridium uses 86.4\u00b0.",

  pattern:
    "Walker-delta: all planes rotate in the same direction, moderate cross-plane ISL " +
    "stability. Walker-star: alternating plane direction, produces counter-rotating seam " +
    "with zero cross-plane ISL at high latitudes.",

  planes:
    "Number of orbital planes. More planes improve longitudinal coverage. Total satellites " +
    "= planes \u00d7 satellites per plane.",

  sats_per_plane:
    "Satellites per orbital plane. More satellites per plane improve intra-plane coverage " +
    "and contact time per ground station pass.",

  raan_spacing_deg:
    "Right Ascension of Ascending Node spacing between planes. Default: 360\u00b0 / planes " +
    "for even distribution.",

  phase_offset_deg:
    "Phase offset between adjacent planes. Controls the relative positioning of satellites " +
    "in neighboring planes. Affects cross-plane ISL geometry and ground station handoff patterns.",

  polar_seam:
    "Enable the seam between the last and first plane for Walker-star constellations. The " +
    "seam plane pair counter-rotates, causing ISL dropouts at high latitudes. This is the " +
    "defining phenomenon of Iridium-style polar orbits.",
};

export const SATELLITE_TYPE_HELP: Record<string, string> = {
  isl_count:
    "Number of inter-satellite link terminals per satellite. Iridium NEXT has 4 (2 " +
    "intra-plane, 2 cross-plane). Starlink Gen2 has 4 optical. Fewer terminals reduce " +
    "ISL fanout and affect routing redundancy.",

  isl_type:
    "RF terminals have lower tracking rate requirements but shorter range and lower " +
    "bandwidth. Optical terminals have longer range and higher bandwidth but require " +
    "more precise pointing.",

  max_range_km:
    "Maximum distance at which this terminal can maintain a link. Determines which " +
    "satellite pairs can form ISLs based on their current separation. Iridium NEXT: " +
    "4400 km. Starlink Gen2: ~5000 km.",

  max_tracking_rate_deg_s:
    "Maximum angular velocity the terminal can track. Cross-plane ISLs at high latitudes " +
    "have high angular velocity as planes converge. If the angular rate exceeds this value, " +
    "the OME declares the link infeasible. This is the physical cause of the polar seam. " +
    "Low values (< 2 deg/s) will cause significant ISL dropouts on polar orbits.",

  field_of_regard_deg:
    "Half-angle of the cone within which the terminal can point. ISLs outside this cone " +
    "are infeasible regardless of range. 120\u00b0 means the terminal can reach anything " +
    "within 60\u00b0 of boresight. 360\u00b0 = omnidirectional (no constraint).",

  bandwidth_mbps:
    "Per-terminal link bandwidth. Affects FRR routing metric calculations " +
    "(reference_bandwidth / bandwidth = isis metric or ospf cost).",

  ground_terminal_count:
    "Number of ground terminals per satellite. Most LEO satellites have 1. More terminals " +
    "enable simultaneous multi-ground-station contacts.",
};

export const GROUND_STATION_HELP: Record<string, string> = {
  min_elevation_deg:
    "Minimum angle above the horizon for a satellite to be considered accessible. " +
    "0\u00b0 is theoretical maximum visibility. 5\u00b0 is practical RF horizon. " +
    "25\u201340\u00b0 is used operationally to ensure adequate link margin. Lower values " +
    "give more contact time but worse link quality during low-elevation passes.",

  scheduling_policy:
    "Highest-elevation prefers the satellite directly overhead (best link quality). " +
    "Longest-pass prefers the satellite that will remain visible longest (fewer handoffs, " +
    "better routing stability).",

  terminal_count:
    "Number of simultaneous satellite contacts this ground station can maintain. Most " +
    "ground stations track one satellite at a time. Multi-antenna installations can " +
    "track 2\u20134 simultaneously.",
};
