// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, renderHook } from "@testing-library/react";
import type { ComponentProps, ReactElement, RefObject } from "react";
import * as THREE from "three";
import { Constellation } from "../Constellation";
import { buildAreaColoring } from "../../../routing/instances";
import { clearPositions } from "../positions";
import type { NodeState } from "../../../types";
import { catalogEarthEphemeris } from "../../../sim/__tests__/bodyModelFixture";

const frame = vi.hoisted(() => ({ callback: () => {} }));
vi.mock("@react-three/fiber", () => ({
  useFrame: (callback: () => void) => {
    frame.callback = callback;
  },
}));
vi.mock("../BodyFrame", () => ({
  useBodyFrame: () => ({ id: "earth", radiusRender: 1, kmPerRenderUnit: 1000 }),
}));
vi.mock("../../../sim/simClock", () => ({ interpolatedSimTimeMs: () => 1735689600000 }));
vi.mock("../../../sim/workerBridge", () => ({
  isWorkerReady: () => true,
  requestPropagate: vi.fn(),
  readPosition: (_id: string, _time: number, target: THREE.Vector3) => {
    Object.assign(target, { x: 6, y: 0, z: 0 });
    return true;
  },
}));

afterEach(() => {
  cleanup();
  clearPositions();
});

function hits(mesh: THREE.InstancedMesh, x: number): number {
  const ray = new THREE.Raycaster(new THREE.Vector3(x, 5, 0), new THREE.Vector3(0, -1, 0));
  const intersections: THREE.Intersection[] = [];
  mesh.raycast(ray, intersections);
  return intersections.length;
}

it.each(["snapshot", "frame"] as const)(
  "Constellation keeps relocated satellites pickable after a %s update",
  (update) => {
    const node = {
      node_id: "sat",
      node_type: "satellite",
      lat_deg: 0,
      lon_deg: 0,
      alt_km: 100,
      reference_body: "earth",
      frame_id: "earth",
      plane: 0,
      slot: 0,
      routing_instances: [] as NodeState["routing_instances"],
      role: "router",
    } as NodeState;
    const ephemeris = catalogEarthEphemeris();
    ephemeris.nodes = {
      sat: {
        type: "keplerian",
        propagator: "two-body",
        semi_major_axis_km: 6928,
        eccentricity: 0,
        inclination_deg: 53,
        raan_deg: 0,
        argument_of_perigee_deg: 0,
        mean_anomaly_deg: 0,
        plane: 0,
        slot: 0,
        reference_body: "earth",
        frame_id: "earth",
      },
    };
    const props: ComponentProps<typeof Constellation> = {
      nodes: [],
      ephemeris,
      colorMode: "plane",
      relations: null,
      regimeById: new Map(),
      areaColoring: buildAreaColoring([], null),
      onSelect: vi.fn(),
      onFocusNode: vi.fn(),
      onTogglePin: vi.fn(),
      onHover: vi.fn(),
    };
    const { result, rerender, unmount } = renderHook((p) => Constellation(p), { initialProps: props });
    const element = result.current as ReactElement<{
      ref: RefObject<THREE.InstancedMesh>;
      args: [THREE.SphereGeometry, THREE.MeshBasicMaterial, number];
    }>;
    const mesh = new THREE.InstancedMesh(...element.props.args);
    element.props.ref.current = mesh;
    try {
      rerender({ ...props, nodes: [node] });
      mesh.updateMatrixWorld(true);
      expect(hits(mesh, 1.1)).toBeGreaterThan(0);
      expect(mesh.boundingSphere).not.toBeNull();
      if (update === "snapshot") {
        rerender({ ...props, nodes: [{ ...node, alt_km: 5000 }] });
      } else {
        frame.callback();
      }
      expect(hits(mesh, 6)).toBeGreaterThan(0);
      expect(hits(mesh, 1.1)).toBe(0);
    } finally {
      unmount();
      mesh.dispose();
    }
  },
);
