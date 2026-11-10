// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.

import { describe, expect, it } from "vitest";
import {
  isGroundLinkState,
  isGroundNode,
  selectionTypeForNodeId,
} from "../networkIdentity";
import type { LinkState, NodeState } from "../generated/vsApi";

function node(node_id: string, node_type: NodeState["node_type"]): NodeState {
  return {
    node_id,
    node_type,
    lat_deg: 0,
    lon_deg: 0,
    alt_km: 0,
    vel_x_km_s: null,
    vel_y_km_s: null,
    vel_z_km_s: null,
    plane: null,
    slot: null,
    routing_instances: [],
    role: "router",
    isl_count: 0,
    gnd_count: 0,
    prefix: null,
    addresses: [],
    min_elevation_deg: null,
    beam_falloff_exponent: null,
    reference_body: "earth",
    frame_id: "earth",
    tenant_id: "default",
    segment_id: "earth",
    local_node_id: node_id,
    namespace: "earth",
    tags: [],
  };
}

function link(link_type: LinkState["link_type"]): LinkState {
  return {
    node_a: "ground-gs-santiago",
    node_b: "meo-sat-p00s00",
    state: "active",
    scheduling_state: "active",
    link_type,
    link_reason: null,
    link_rule_id: null,
    topology_mode: null,
    endpoint_segments: null,
    teardown_remaining_ticks: null,
    successor_pair: null,
    contact_id: null,
    latency_a_to_b_ms: 1,
    latency_b_to_a_ms: 1,
    transmit_mbps_a: 1,
    receive_mbps_a: 1,
    transmit_mbps_b: 1,
    receive_mbps_b: 1,
    range_km: 1,
    range_sim_time: "2026-06-08T00:04:15Z",
    traffic_load_pct: null,
    interface_a: "a",
    interface_b: "b",
  };
}

describe("network identity classification", () => {
  it("classifies nodes from node_type, not id prefix", () => {
    expect(isGroundNode(node("ground-gs-santiago", "ground_station"))).toBe(true);
    expect(isGroundNode(node("leo-sat-p00s00", "satellite"))).toBe(false);
  });

  it("classifies ground links from link_type, not endpoint ids", () => {
    expect(isGroundLinkState(link("ground"))).toBe(true);
    expect(isGroundLinkState(link("isl"))).toBe(false);
    expect(isGroundLinkState(link("inter_constellation"))).toBe(false);
    expect(isGroundLinkState(link("inter_body_relay"))).toBe(false);
  });

  it("fails loudly on a link_type another VS-API release sends", () => {
    const unknown = "intra_plane_isl" as unknown as LinkState["link_type"];
    expect(() => isGroundLinkState(link(unknown))).toThrow(/Unknown link_type/);
  });

  it("fails loudly on a kind named like an object's own built-in property", () => {
    for (const name of ["constructor", "toString", "hasOwnProperty", "__proto__"]) {
      const kind = name as unknown as LinkState["link_type"];
      expect(() => isGroundLinkState(link(kind))).toThrow(/Unknown link_type/);
      const nodeType = name as unknown as NodeState["node_type"];
      expect(() => isGroundNode({ node_type: nodeType })).toThrow(/Unknown node_type/);
    }
  });

  it("selects namespaced ground nodes as ground station cards", () => {
    const nodes = [
      node("ground-gs-santiago", "ground_station"),
      node("meo-sat-p00s00", "satellite"),
    ];
    expect(selectionTypeForNodeId("ground-gs-santiago", nodes)).toBe("ground_station");
    expect(selectionTypeForNodeId("meo-sat-p00s00", nodes)).toBe("satellite");
  });
});
