// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { describe, expect, it } from "vitest";
import { filterSnapshotForRender } from "../renderSnapshot";
import type { LinkState, NodeState, StateSnapshot } from "../../types";

function node(node_id: string, segment_id: string, plane: number | null): NodeState {
  return {
    node_id,
    node_type: plane === null ? "ground_station" : "satellite",
    lat_deg: 0,
    lon_deg: 0,
    alt_km: plane === null ? 0 : 550,
    vel_x_km_s: plane === null ? null : 0,
    vel_y_km_s: plane === null ? null : 0,
    vel_z_km_s: plane === null ? null : 0,
    plane,
    slot: plane === null ? null : 0,
    routing_area: null,
    neighbor_count: 0,
    isl_count: 0,
    gnd_count: 0,
    prefix: null,
    min_elevation_deg: null,
    beam_falloff_exponent: null,
    segment_id,
    tags: [segment_id],
    reference_body: "earth",
    frame_id: "earth",
  };
}

function link(node_a: string, node_b: string): LinkState {
  return {
    node_a,
    node_b,
    state: "active",
    link_type: "inter_constellation",
    link_reason: null,
    latency_ms: 1,
    bandwidth_mbps: 1000,
    range_km: 100,
    traffic_load_pct: null,
    interface_a: "isl0",
    interface_b: "isl0",
  };
}

function snapshot(): StateSnapshot {
  return {
    sim_time: "2026-01-01T00:00:00Z",
    wall_time: "2026-01-01T00:00:00Z",
    schema_version: 1,
    session_id: "test",
    nodes: [
      node("leo-sat-p00s00", "leo", 0),
      node("meo-sat-p00s00", "meo", 1),
      node("ground-gs-denver", "ground", null),
    ],
    links: [
      link("leo-sat-p00s00", "meo-sat-p00s00"),
      link("ground-gs-denver", "leo-sat-p00s00"),
    ],
    kernel_actual_pairs: [
      ["leo-sat-p00s00", "meo-sat-p00s00"],
      ["ground-gs-denver", "leo-sat-p00s00"],
    ],
    traced_paths: [
      { flow_id: "kept", src_node: "ground-gs-denver", dst_node: "leo-sat-p00s00", hops: ["ground-gs-denver", "leo-sat-p00s00"] },
      { flow_id: "hidden", src_node: "leo-sat-p00s00", dst_node: "meo-sat-p00s00", hops: ["leo-sat-p00s00", "meo-sat-p00s00"] },
      {
        flow_id: "hidden-reverse",
        src_node: "ground-gs-denver",
        dst_node: "leo-sat-p00s00",
        hops: ["ground-gs-denver", "leo-sat-p00s00"],
        reverse_hops: ["leo-sat-p00s00", "meo-sat-p00s00"],
      },
    ],
    active_flows: [
      {
        flow_id: "kept",
        src_node: "ground-gs-denver",
        dst_node: "leo-sat-p00s00",
        protocol: "udp",
        probe_type: "continuous",
      },
      {
        flow_id: "hidden",
        src_node: "leo-sat-p00s00",
        dst_node: "meo-sat-p00s00",
        protocol: "udp",
        probe_type: "continuous",
      },
    ],
    recent_events: [],
    network_health: {
      status: "converged",
      converging_since_ms: null,
      unreachable_flows: 0,
      last_convergence_ms: null,
    },
    routing_stack: "isis-plain",
    constellation_name: "test",
    session_status: "ready",
    session_status_detail: null,
    playback_paused: false,
    playback_speed: 1,
    stale: false,
  };
}

describe("filterSnapshotForRender", () => {
  it("filters nodes, links, kernel pairs, and traced paths by visible segment", () => {
    const filtered = filterSnapshotForRender(snapshot(), new Set(["leo", "ground"]), null);

    expect(filtered?.nodes.map((n) => n.node_id)).toEqual([
      "leo-sat-p00s00",
      "ground-gs-denver",
    ]);
    expect(filtered?.links.map((l) => [l.node_a, l.node_b])).toEqual([
      ["ground-gs-denver", "leo-sat-p00s00"],
    ]);
    expect(filtered?.kernel_actual_pairs).toEqual([
      ["ground-gs-denver", "leo-sat-p00s00"],
    ]);
    expect(filtered?.traced_paths.map((path) => path.flow_id)).toEqual(["kept"]);
    expect(filtered?.active_flows.map((flow) => flow.flow_id)).toEqual(["kept"]);
  });

  it("filters satellite planes without hiding ground stations", () => {
    const filtered = filterSnapshotForRender(snapshot(), null, new Set([0]));

    expect(filtered?.nodes.map((n) => n.node_id)).toEqual([
      "leo-sat-p00s00",
      "ground-gs-denver",
    ]);
  });
});
