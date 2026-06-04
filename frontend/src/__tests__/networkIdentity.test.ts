// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.

import { describe, expect, it } from "vitest";
import {
  isGroundLinkState,
  isGroundNode,
  selectionTypeForNodeId,
} from "../networkIdentity";
import type { LinkState, NodeState } from "../types";

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
    routing_area: null,
    neighbor_count: 0,
    isl_count: 0,
    gnd_count: 0,
    prefix: null,
    min_elevation_deg: null,
    beam_falloff_exponent: null,
  };
}

function link(link_type: LinkState["link_type"]): LinkState {
  return {
    node_a: "ground-gs-santiago",
    node_b: "meo-sat-p00s00",
    state: "active",
    link_type,
    link_reason: null,
    latency_ms: 1,
    bandwidth_mbps: 1,
    range_km: 1,
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
    expect(isGroundLinkState(link("intra_plane_isl"))).toBe(false);
    expect(isGroundLinkState(link("cross_plane_isl"))).toBe(false);
  });

  it("fails loudly when link_type is missing", () => {
    expect(() => isGroundLinkState(link(null))).toThrow(/link_type is required/);
  });

  it("fails loudly when link_type is not in the runtime vocabulary", () => {
    expect(() => isGroundLinkState(link("link_rule:earth-access"))).toThrow(/Unknown link_type/);
    expect(() => isGroundLinkState(link("not-real"))).toThrow(/Unknown link_type/);
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
