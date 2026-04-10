// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** VS-API types — mirrors lib/nodalarc/models/vs_api.py exactly.
 *  Field names are snake_case to match Pydantic JSON output.
 */

export interface NodeState {
  node_id: string;
  node_type: string; // "satellite" | "ground_station"
  lat_deg: number;
  lon_deg: number;
  alt_km: number;
  vel_x_km_s: number | null;
  vel_y_km_s: number | null;
  vel_z_km_s: number | null;
  plane: number | null;
  slot: number | null;
  routing_area: string | null;
  neighbor_count: number;
  isl_count: number;
  gnd_count: number;
  prefix: string | null;
  min_elevation_deg: number | null;
  beam_falloff_exponent: number | null;
}

export interface LinkState {
  node_a: string;
  node_b: string;
  state: string; // "active" | "inactive"
  link_type: string | null;
  link_reason: string | null;
  latency_ms: number;
  bandwidth_mbps: number;
  range_km: number;
  traffic_load_pct: number | null;
  interface_a: string;
  interface_b: string;
}

export interface TracedPath {
  flow_id: string;
  src_node: string;
  dst_node: string;
  hops: string[];
  reverse_hops?: string[];
  hop_rtts?: (number | null)[];
  reverse_hop_rtts?: (number | null)[];
  rtt_ms?: number;
  reverse_rtt_ms?: number;
  asymmetry_detected?: boolean;
  method?: string;
  path_valid_until?: string;
  path_valid_seconds?: number;
  traced_at?: string;
}

export interface NetworkHealth {
  status: string; // "converged" | "converging" | "degraded"
  converging_since_ms: number | null;
  unreachable_flows: number;
  last_convergence_ms: number | null;
}

export interface ActiveFlow {
  flow_id: string;
  src_node: string;
  dst_node: string;
  protocol: string;
  probe_type: string;
}

export interface RecentEvent {
  sim_time: string;
  node_id: string;
  event_type: string;
  summary: string;
}

export interface StateSnapshot {
  sim_time: string;
  wall_time: string;
  schema_version: number;
  nodes: NodeState[];
  links: LinkState[];
  traced_paths: TracedPath[];
  active_flows: ActiveFlow[];
  recent_events: RecentEvent[];
  network_health: NetworkHealth;
  routing_stack: string | null;
  constellation_name: string | null;
  session_status: string | null;
  session_status_detail: string | null;
  playback_paused: boolean;
  playback_speed: number;
  stale: boolean;
}

// Distributed ephemeris model (PRD v0.71)
// Re-exported from sim/ephemeris.ts for convenience
export type {
  SessionEphemeris,
  EphemerisNode,
  EphemerisNodeKeplerian,
  EphemerisNodeFixed,
  PlaybackStateMsg,
} from "./sim/ephemeris";

export interface SessionInfo {
  name: string;
  file: string;
  constellation: string;
  routing_stack: string;
  active: boolean;
}

/** App-level selection state */
export type SelectionType = "satellite" | "ground_station" | "link" | null;

export interface Selection {
  type: SelectionType;
  id: string; // node_id or "nodeA:nodeB" for links
}

/** View modes */
export type ViewMode = "globe" | "topology" | "split";
export type ColorMode = "area" | "plane";
export type GlobeMode = "blue-marble" | "day-night";

/** Reference frame for the globe view.
 *  - "earth-fixed": Earth static, satellites trace ground tracks, stars rotate
 *    at sidereal rate beneath the fixed Earth. Current-behavior default.
 *  - "earth-inertial": observer fixed in inertial space, Earth visibly rotates
 *    at sidereal rate, satellites visibly traverse orbits, stars stationary.
 *
 *  Namespace reserves "earth-*" prefix for future "moon-*", "sun-*", and
 *  rotating-barycenter-frame values. See specs/eci-view-plan.md §9. */
export type ReferenceFrame = "earth-fixed" | "earth-inertial";
