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
}

export interface TracedPath {
  flow_id: string;
  src_node: string;
  dst_node: string;
  hops: string[];
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
}

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
