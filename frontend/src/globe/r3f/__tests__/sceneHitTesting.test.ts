// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only

import { beforeEach, describe, expect, it } from "vitest";
import * as THREE from "three";
import { clearPositions, setBodyFrame, setNodeLocalPosition } from "../positions";
import {
  bodyHitRadiusPx,
  nodeHitRadiusPx,
  pickSceneAtScreenPoint,
  pointToSegment2D,
} from "../sceneHitTesting";
import type { LinkState, NodeState } from "../../../generated/vsApi";

const rect = { width: 1000, height: 1000 };

function camera(): THREE.PerspectiveCamera {
  const c = new THREE.PerspectiveCamera(45, 1, 0.1, 1000);
  c.position.set(0, 0, 100);
  c.lookAt(0, 0, 0);
  c.updateProjectionMatrix();
  c.updateMatrixWorld();
  return c;
}

function node(node_id: string, node_type: "satellite" | "ground_station" = "satellite"): NodeState {
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

function link(node_a: string, node_b: string): LinkState {
  return {
    node_a,
    node_b,
    state: "active",
    scheduling_state: "active",
    link_type: "isl",
    link_reason: null,
    link_rule_id: null,
    topology_mode: null,
    endpoint_segments: null,
    teardown_remaining_ticks: null,
    successor_pair: null,
    contact_id: null,
    latency_a_to_b_ms: 1,
    latency_b_to_a_ms: 1,
    transmit_mbps_a: 100,
    receive_mbps_a: 100,
    transmit_mbps_b: 100,
    receive_mbps_b: 100,
    range_km: 1,
    range_sim_time: "2026-06-08T00:04:15Z",
    traffic_load_pct: null,
    interface_a: "a",
    interface_b: "b",
  };
}

describe("scene screen-space hit testing", () => {
  beforeEach(() => {
    clearPositions();
    setBodyFrame("earth", new THREE.Group(), 100);
  });

  it("keeps tiny rendered nodes selectable with a bounded screen-space target", () => {
    setNodeLocalPosition("sat-a", "earth", 0, 0, 0);

    const hit = pickSceneAtScreenPoint({
      xPx: 514,
      yPx: 500,
      camera: camera(),
      rect,
      nodes: [node("sat-a")],
      links: [],
      bodies: [],
      showIslLinks: true,
      showGroundLinks: true,
    });

    expect(hit?.kind).toBe("node");
    expect(hit?.kind === "node" ? hit.node.node_id : null).toBe("sat-a");
  });

  it("uses node priority before beam hits at the same screen point", () => {
    setNodeLocalPosition("sat-a", "earth", 0, 0, 0);
    setNodeLocalPosition("sat-b", "earth", 30, 0, 0);

    const hit = pickSceneAtScreenPoint({
      xPx: 500,
      yPx: 500,
      camera: camera(),
      rect,
      nodes: [node("sat-a"), node("sat-b")],
      links: [link("sat-a", "sat-b")],
      bodies: [],
      showIslLinks: true,
      showGroundLinks: true,
    });

    expect(hit?.kind).toBe("node");
  });

  it("picks a small body just outside its projected limb", () => {
    const hit = pickSceneAtScreenPoint({
      xPx: 584,
      yPx: 500,
      camera: camera(),
      rect,
      nodes: [],
      links: [],
      bodies: [{ id: "luna", center: new THREE.Vector3(0, 0, 0), radius: 5 }],
      showIslLinks: true,
      showGroundLinks: true,
    });

    expect(hit).toEqual({ kind: "body", bodyId: "luna" });
  });

  it("keeps hit radii finite and bounded", () => {
    expect(nodeHitRadiusPx(0)).toBe(28);
    expect(nodeHitRadiusPx(100)).toBe(48);
    expect(bodyHitRadiusPx(0)).toBe(24);
    expect(bodyHitRadiusPx(100)).toBe(124);
    expect(pointToSegment2D(5, 5, 0, 0, 10, 0)).toBe(5);
  });
});
