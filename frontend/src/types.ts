// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** VS-API types — mirrors lib/nodalarc/models/vs_api.py exactly.
 *  Field names are snake_case to match Pydantic JSON output.
 */

import type { ActuationState } from "./explain/reasons";

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
  /** Celestial body this node is anchored to (earth | luna | mars). Optional for forward-compat
   *  with pre-parameterization snapshots; consumers default to "earth". */
  reference_body?: string;
  /** Placement frame id from the resolved session. Defaults to the reference body for old payloads. */
  frame_id?: string;
  /** Owning tenant (multi-tenant from day one). Optional; consumers default to "default". */
  tenant_id?: string;
  /** Segment metadata from the resolved session. Used for grouping/filtering, not runtime identity. */
  segment_id?: string | null;
  local_node_id?: string | null;
  namespace?: string | null;
  tags?: string[];
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
  link_rule_id?: string | null;
  topology_mode?: string | null;
  endpoint_segments?: [string, string] | null;
  scheduling_state?: string;
  teardown_remaining_ticks?: number | null;
  successor_pair?: [string, string] | null;
}

export interface LinkDecisionTrace {
  node_a: string;
  node_b: string;
  link_type: string;
  state: string;
  interface_a: string;
  interface_b: string;
  reason: string | null;
  geometry_authority: string;
  authority_source: string;
  authority_sim_time: string;
  authority_sequence: number | null;
  authority_age_ms: number | null;
  range_km: number;
  orbital_one_way_ms: number;
  substrate_rtt_ms: number | null;
  substrate_one_way_ms: number | null;
  netem_one_way_ms: number | null;
  rtt_to_one_way_policy: string | null;
  link_rule_id?: string | null;
  topology_mode?: string | null;
  endpoint_segments?: [string, string] | null;
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

export interface OpsEvent {
  timestamp: string;
  session_id: string;
  source: string;
  hostname: string;
  level: string;
  code: string;
  message: string;
  details?: Record<string, unknown> | null;
}


export interface ActuationNotice {
  gs_id: string;
  actuation_state: ActuationState;
  reason_code: string;
  message: string;
  since: string | null;
  blocking_new_ground_link_up: boolean;
  affected_pairs: string[][];
  desired_pairs_for_gs: string[][];
  actual_pairs_for_gs: string[][];
  ome_visible_scheduled_pairs_for_gs: string[][];
  recovery_status: Record<string, unknown>;
  last_event: Record<string, unknown>;
}

export interface ActuationHealthGroundStation {
  gs_id: string;
  actuation_state: ActuationState;
  since: string | null;
  reason_code: string | null;
  blocking_new_ground_link_up: boolean;
  recovery_status: Record<string, unknown>;
  last_event: Record<string, unknown>;
}

export interface ActuationHealthInstance {
  scheduler_instance_id: string;
  hostname: string;
  status: string;
  ground_stations: ActuationHealthGroundStation[];
}

export interface ActuationHealth {
  session_id: string;
  wiring_generation: string;
  scheduler_instances: ActuationHealthInstance[];
}

export interface AlmanacState {
  last_topology_state_id: string | null;
  last_push_sim_time: string | null;
  last_push_wall_time: number | null;
  nodes_succeeded: number;
  nodes_failed: number;
  deviation_count: number;
  recomputation_count: number;
  nodalpath_active: boolean;
}

export interface StateSnapshot {
  sim_time: string;
  wall_time: string;
  schema_version: number;
  session_id: string;
  nodes: NodeState[];
  links: LinkState[];
  /** Scheduler-verified kernel-PROVEN pairs (ordered [a,b]); distinct from `links` (OME's
   *  admin/carrier model). The globe renders proven links solid, unproven OME-desired links
   *  dimmed — so a beam never reads connected while the card says in_flight/faulted. */
  kernel_actual_pairs?: [string, string][];
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
  actuation_notices?: ActuationNotice[];
  actuation_health?: ActuationHealth | null;
  ops_events?: OpsEvent[];
  debug_events?: OpsEvent[];
  debug_sources?: string[];
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
export type ViewMode = "globe" | "topology" | "split" | "dashboard";
export type ColorMode = "area" | "plane";
export type GlobeMode = "blue-marble" | "day-night" | "political";

/** Reference frame for the globe view.
 *  - "earth-fixed": Earth static, satellites trace ground tracks, stars rotate
 *    at sidereal rate beneath the fixed Earth. Current-behavior default.
 *  - "earth-inertial": observer fixed in inertial space, Earth visibly rotates
 *    at sidereal rate, satellites visibly traverse orbits, stars stationary.
 *
 *  Namespace reserves "earth-*" prefix for future "moon-*", "sun-*", and
 *  rotating-barycenter-frame values. See specs/eci-view-plan.md §9. */
export type ReferenceFrame = "earth-fixed" | "earth-inertial";
