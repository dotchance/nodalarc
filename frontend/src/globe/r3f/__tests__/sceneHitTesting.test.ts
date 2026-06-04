// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.

import { beforeEach, describe, expect, it } from "vitest";
import * as THREE from "three";
import type { LinkState, NodeState } from "../../../types";
import { clearPositions, setBodyFrame, setNodeLocalPosition } from "../positions";
import {
  bodyHitRadiusPx,
  nodeHitRadiusPx,
  pickSceneAtScreenPoint,
  pointToSegment2D,
} from "../sceneHitTesting";

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
    routing_area: null,
    neighbor_count: 0,
    isl_count: 0,
    gnd_count: 0,
    prefix: null,
    min_elevation_deg: null,
    beam_falloff_exponent: null,
    reference_body: "earth",
  };
}

function link(node_a: string, node_b: string): LinkState {
  return {
    node_a,
    node_b,
    state: "active",
    link_type: "isl",
    link_reason: null,
    latency_ms: 1,
    bandwidth_mbps: 100,
    range_km: 1,
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
    expect(nodeHitRadiusPx(0)).toBe(16);
    expect(nodeHitRadiusPx(100)).toBe(30);
    expect(bodyHitRadiusPx(0)).toBe(24);
    expect(bodyHitRadiusPx(100)).toBe(124);
    expect(pointToSegment2D(5, 5, 0, 0, 10, 0)).toBe(5);
  });
});
