// ── Topology (from GET /api/v1/topology/current) ────────────────────────────

export interface ConsoleNode {
    node_id: string;
    node_type: "satellite" | "ground_station";
    plane: number | null;
    slot: number | null;
    routing_area: string | null;
    neighbor_count: number;
    isl_count: number;
    gnd_count: number;
    prefix: string | null;
}

export interface ConsoleLink {
    node_a: string;
    node_b: string;
    state: "active" | "visible_unscheduled" | "inactive";
    link_type: "isl" | "ground";
    visible?: boolean;
    scheduled?: boolean;
    range_km?: number;
}

export interface TopologySnapshot {
    available: boolean;
    topology_state_id?: string;
    sim_time?: string;
    nodes?: ConsoleNode[];
    links?: ConsoleLink[];
}

// ── Console state (from GET /api/status) ────────────────────────────────────

export interface PushRecord {
    topology_state_id: string;
    sim_time: string;
    nodes_attempted: number;
    nodes_succeeded: number;
    nodes_failed: number;
    nodes_skipped: number;
    push_duration_ms: number;
    failed_nodes: string[];
}

export interface DeviationRecord {
    sim_time: string;
    topology_state_id: string;
    node_a: string;
    node_b: string;
    reason: string;
}

export interface EventRecord {
    wall_time: string;
    event_type: "TRANSITION" | "PUSH" | "DEVIATE" | "RECOMPUTE";
    summary: string;
    details: Record<string, unknown>;
}

export interface ConsoleStateSnapshot {
    session_path: string;
    transport: string;
    dry_run: boolean;
    start_wall_time: string;
    nodes_in_registry: number;
    transition_count: number;
    deviation_count: number;
    recomputation_count: number;
    last_topology_state_id: string | null;
    last_sim_time: string | null;
    push_history: PushRecord[];
    almanac_history: unknown[];
    deviation_history: DeviationRecord[];
    event_log: EventRecord[];
}

// ── Node detail (from GET /api/v1/node/{node_id}/state) ─────────────────────

export interface ForwardingEntry {
    destination: string;
    next_hop: string;
    outgoing_label: number | null;
    incoming_label: number | null;
    operation: "push" | "swap" | "pop" | null;
}

export interface NodeStateDetail {
    available: boolean;
    node_id?: string;
    topology_state_id?: string;
    forwarding_entries?: ForwardingEntry[];
    reason?: string;
}

// ── Timeline (from GET /api/v1/timeline) ────────────────────────────────────

export interface TimelineTick {
    sim_time: string;
    topology_state_id: string;
    node_count: number;
    is_future: boolean;
    push_succeeded: boolean | null;
    push_failed_count: number;
    had_deviation: boolean;
    node_count_delta: number | null;
}

export interface TimelineResponse {
    available: boolean;
    tick_count: number;
    lookahead_status: "disabled" | "starting" | "computing" | "waiting" | "complete";
    ticks: TimelineTick[];
}

// ── Historical topology (from GET /api/v1/topology/at/{sim_time}) ────────────

export interface HistoricalTopologyResponse extends TopologySnapshot {
    is_historical: boolean;
    is_future: boolean;
    links_available: boolean;
}

// ── Path (from GET /api/v1/path) ─────────────────────────────────────────────

export interface PathHop {
    node_id: string;
    node_type: "satellite" | "ground_station";
    in_label: number | null;
    out_label: number | null;
    action: "push" | "swap" | "pop" | null;
    out_interface: string | null;
    latency_to_next_ms: number | null;
}

export interface PathResult {
    src: string;
    dst: string;
    hops: PathHop[];
    total_latency_ms: number;
    method: "derived" | "probed";
    sim_time: string;
    topology_state_id: string;
    reachable: boolean;
    unreachable_reason: string | null;
}

// ── Inspection (from GET /api/v1/inspect/*) ──────────────────────────────────

export interface BindingDiff {
    in_label: number;
    kind: "missing" | "extra" | "mismatch";
    planned_action: string | null;
    planned_out_label: number | null;
    planned_out_interface: string | null;
    observed_action: string | null;
    observed_out_label: number | null;
    observed_out_interface: string | null;
}

export interface IngressDiff {
    dst_prefix: string;
    kind: "missing" | "extra" | "mismatch";
    planned_push_label: number | null;
    planned_out_interface: string | null;
    observed_push_label: number | null;
    observed_out_interface: string | null;
}

export interface InspectionNodeResult {
    node_id: string;
    reachable: boolean;
    status_topology_state_id: string | null;
    status_total_entries: number | null;
    has_deviation: boolean;
    error_message: string | null;
    binding_diffs: BindingDiff[];
    ingress_diffs: IngressDiff[];
}

export interface InspectionRunSummary {
    run_id: string;
    trigger: string;
    topology_state_id: string;
    started_at: string;
    completed_at: string | null;
    nodes_inspected: number;
    nodes_reachable: number;
    nodes_with_deviations: number;
    nodes_unreachable: number;
}

export interface InspectionRunDetail extends InspectionRunSummary {
    node_results: InspectionNodeResult[];
}

// ── D3 graph nodes (ConsoleNode extended with computed layout position) ──────

export interface GraphNode extends ConsoleNode {
    x: number;
    y: number;
}

export interface GraphLink extends ConsoleLink {
    sourceNode: GraphNode;
    targetNode: GraphNode;
}
