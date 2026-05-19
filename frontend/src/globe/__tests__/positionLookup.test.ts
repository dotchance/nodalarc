// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import * as THREE from "three";
import { getNodeLocalPosition, getNodeWorldPosition, setEarthFrame } from "../positionLookup";
import { updateSatellites, animateSatellites, setEphemeris, getSatellites, getPositionCache } from "../satellites";
import { updateGroundStations, getGroundStations } from "../groundStations";
import { onSnapshot } from "../../sim/simClock";
import { SCENE_EARTH_RADIUS, SCENE_KM_PER_UNIT } from "../../sim/orbitalMath";
import type { SessionEphemeris } from "../../sim/ephemeris";
import type { NodeState } from "../../types";

// JSDOM has no canvas implementation. Stub getContext to return a
// minimal mock so updateSatellites can create glow textures.
HTMLCanvasElement.prototype.getContext = function () {
  return {
    createRadialGradient: () => ({ addColorStop: () => {} }),
    fillRect: () => {},
    fillStyle: "",
    beginPath: () => {},
    arc: () => {},
    fill: () => {},
    moveTo: () => {},
    lineTo: () => {},
    quadraticCurveTo: () => {},
    stroke: () => {},
    strokeStyle: "",
    lineWidth: 0,
  } as any;
} as any;

function makeSatNode(id: string, lat: number, lon: number, alt: number): NodeState {
  return {
    node_id: id, node_type: "satellite",
    lat_deg: lat, lon_deg: lon, alt_km: alt,
    vel_x_km_s: null, vel_y_km_s: null, vel_z_km_s: null,
    plane: 0, slot: 0, routing_area: null,
    neighbor_count: 0, isl_count: 0, gnd_count: 0,
    prefix: null, min_elevation_deg: null, beam_falloff_exponent: null,
  };
}

function makeGsNode(id: string, lat: number, lon: number): NodeState {
  return {
    node_id: id, node_type: "ground_station",
    lat_deg: lat, lon_deg: lon, alt_km: 0,
    vel_x_km_s: null, vel_y_km_s: null, vel_z_km_s: null,
    plane: null, slot: null, routing_area: null,
    neighbor_count: 0, isl_count: 0, gnd_count: 0,
    prefix: null, min_elevation_deg: 25, beam_falloff_exponent: null,
  };
}

describe("positionLookup", () => {
  let earthFrame: THREE.Group;
  let labelContainer: HTMLDivElement;

  beforeEach(() => {
    earthFrame = new THREE.Group();
    earthFrame.name = "earthFrame";
    setEarthFrame(earthFrame);
    labelContainer = document.createElement("div");
  });

  afterEach(() => {
    // Clear satellite and GS state between tests.
    // InstancedMesh is managed by satellites.ts internally.
    getSatellites().clear();
    for (const [, entry] of getGroundStations()) {
      earthFrame.remove(entry.sprite);
      earthFrame.remove(entry.cone);
      earthFrame.remove(entry.coneOutline);
      entry.label.remove();
    }
    getGroundStations().clear();
  });

  describe("getNodeLocalPosition", () => {
    it("returns false for nonexistent node", () => {
      const target = new THREE.Vector3(999, 999, 999);
      const found = getNodeLocalPosition("sat-nonexistent", target);
      expect(found).toBe(false);
      expect(target.x).toBe(999);
    });

    it("returns satellite position from positionCache", () => {
      const nodes = [makeSatNode("sat-P00S00", 0, 0, 550)];
      updateSatellites(nodes, earthFrame, "area", "2026-01-01T00:00:00Z");

      const target = new THREE.Vector3();
      const found = getNodeLocalPosition("sat-P00S00", target);
      expect(found).toBe(true);
      expect(target.length()).toBeGreaterThan(0);
    });

    it("returns ground station position", () => {
      const nodes = [makeGsNode("gs-test", 51.0, -1.0)];
      updateGroundStations(nodes, earthFrame, labelContainer);

      const target = new THREE.Vector3();
      const found = getNodeLocalPosition("gs-test", target);
      expect(found).toBe(true);
      expect(target.length()).toBeGreaterThan(0);
    });

    it("position cache matches what updateSatellites wrote", () => {
      const nodes = [makeSatNode("sat-P00S00", 10, 20, 550)];
      updateSatellites(nodes, earthFrame, "area", "2026-01-01T00:00:00Z");

      const target = new THREE.Vector3();
      getNodeLocalPosition("sat-P00S00", target);

      const cache = getPositionCache();
      const entry = getSatellites().get("sat-P00S00")!;
      const idx = entry.instanceIndex * 3;
      expect(target.x).toBe(cache[idx]);
      expect(target.y).toBe(cache[idx + 1]);
      expect(target.z).toBe(cache[idx + 2]);
    });

    it("satellite at equator/prime meridian has positive X, near-zero Y and Z", () => {
      const nodes = [makeSatNode("sat-equator", 0, 0, 550)];
      updateSatellites(nodes, earthFrame, "area", "2026-01-01T00:00:00Z");

      const target = new THREE.Vector3();
      getNodeLocalPosition("sat-equator", target);
      expect(target.x).toBeGreaterThan(50);
      expect(Math.abs(target.y)).toBeLessThan(1);
      expect(Math.abs(target.z)).toBeLessThan(1);
    });

    it("satellite at north pole has positive Y, near-zero X and Z", () => {
      const nodes = [makeSatNode("sat-pole", 90, 0, 550)];
      updateSatellites(nodes, earthFrame, "area", "2026-01-01T00:00:00Z");

      const target = new THREE.Vector3();
      getNodeLocalPosition("sat-pole", target);
      expect(target.y).toBeGreaterThan(50);
      expect(Math.abs(target.x)).toBeLessThan(1);
      expect(Math.abs(target.z)).toBeLessThan(1);
    });
  });

  describe("getNodeWorldPosition", () => {
    it("returns false for nonexistent node", () => {
      const target = new THREE.Vector3(999, 999, 999);
      const found = getNodeWorldPosition("gs-nonexistent", target);
      expect(found).toBe(false);
      expect(target.x).toBe(999);
    });

    it("matches local position when earthFrame has no rotation", () => {
      const nodes = [makeSatNode("sat-P00S00", 30, 45, 550)];
      updateSatellites(nodes, earthFrame, "area", "2026-01-01T00:00:00Z");

      const local = new THREE.Vector3();
      const world = new THREE.Vector3();
      getNodeLocalPosition("sat-P00S00", local);
      getNodeWorldPosition("sat-P00S00", world);

      expect(world.x).toBeCloseTo(local.x, 5);
      expect(world.y).toBeCloseTo(local.y, 5);
      expect(world.z).toBeCloseTo(local.z, 5);
    });

    it("differs from local position when earthFrame is rotated", () => {
      const nodes = [makeSatNode("sat-P00S00", 0, 0, 550)];
      updateSatellites(nodes, earthFrame, "area", "2026-01-01T00:00:00Z");

      earthFrame.rotation.y = Math.PI / 2;
      earthFrame.updateMatrixWorld(true);

      const local = new THREE.Vector3();
      const world = new THREE.Vector3();
      getNodeLocalPosition("sat-P00S00", local);
      getNodeWorldPosition("sat-P00S00", world);

      expect(local.x).toBeGreaterThan(50);
      expect(Math.abs(world.x)).toBeLessThan(1);
      expect(world.length()).toBeCloseTo(local.length(), 3);
    });

    it("preserves distance from origin under earthFrame rotation", () => {
      const nodes = [makeSatNode("sat-P00S00", 45, 90, 550)];
      updateSatellites(nodes, earthFrame, "area", "2026-01-01T00:00:00Z");

      const pos1 = new THREE.Vector3();
      getNodeWorldPosition("sat-P00S00", pos1);
      const dist1 = pos1.length();

      earthFrame.rotation.y = 1.5;
      earthFrame.updateMatrixWorld(true);

      const pos2 = new THREE.Vector3();
      getNodeWorldPosition("sat-P00S00", pos2);
      const dist2 = pos2.length();

      expect(dist2).toBeCloseTo(dist1, 5);
    });
  });

  describe("mixed node types", () => {
    it("resolves satellites and ground stations through the same API", () => {
      const nodes = [
        makeSatNode("sat-P00S00", 0, 0, 550),
        makeGsNode("gs-london", 51.5, -0.1),
      ];
      updateSatellites(nodes, earthFrame, "area", "2026-01-01T00:00:00Z");
      updateGroundStations(nodes, earthFrame, labelContainer);

      const satPos = new THREE.Vector3();
      const gsPos = new THREE.Vector3();
      expect(getNodeLocalPosition("sat-P00S00", satPos)).toBe(true);
      expect(getNodeLocalPosition("gs-london", gsPos)).toBe(true);

      expect(satPos.length()).toBeGreaterThan(gsPos.length());
    });
  });

  describe("animateSatellites integration", () => {
    it("propagates satellite positions from ephemeris via animateSatellites", () => {
      const epoch = "2026-04-01T00:00:00Z";
      const epochUnix = new Date(epoch).getTime() / 1000;

      const ephemeris: SessionEphemeris = {
        epoch_id: 1,
        sim_time: epoch,
        epoch_unix: epochUnix,
        nodes: {
          "sat-P00S00": {
            type: "keplerian",
            altitude_km: 550,
            inclination_deg: 53,
            raan_deg: 0,
            true_anomaly_deg: 0,
            plane: 0,
            slot: 0,
          },
        },
      };

      const nodes = [makeSatNode("sat-P00S00", 0, 0, 550)];
      updateSatellites(nodes, earthFrame, "area", epoch);

      setEphemeris(ephemeris);
      onSnapshot(epoch, performance.now());

      animateSatellites(0.016);

      const posAfter = new THREE.Vector3();
      getNodeLocalPosition("sat-P00S00", posAfter);

      const dist = posAfter.length();
      const expectedDist = SCENE_EARTH_RADIUS + 550 / SCENE_KM_PER_UNIT;
      expect(Math.abs(dist - expectedDist)).toBeLessThan(0.5);
      expect(dist).toBeGreaterThan(SCENE_EARTH_RADIUS);
    });

    it("two satellites at different orbital elements have different positions", () => {
      const epoch = "2026-04-01T00:00:00Z";
      const epochUnix = new Date(epoch).getTime() / 1000;

      const ephemeris: SessionEphemeris = {
        epoch_id: 1,
        sim_time: epoch,
        epoch_unix: epochUnix,
        nodes: {
          "sat-P00S00": {
            type: "keplerian",
            altitude_km: 550,
            inclination_deg: 53,
            raan_deg: 0,
            true_anomaly_deg: 0,
            plane: 0,
            slot: 0,
          },
          "sat-P05S10": {
            type: "keplerian",
            altitude_km: 550,
            inclination_deg: 53,
            raan_deg: 90,
            true_anomaly_deg: 180,
            plane: 5,
            slot: 10,
          },
        },
      };

      const nodes = [
        makeSatNode("sat-P00S00", 0, 0, 550),
        makeSatNode("sat-P05S10", 0, 90, 550),
      ];
      updateSatellites(nodes, earthFrame, "area", epoch);

      setEphemeris(ephemeris);
      onSnapshot(epoch, performance.now());
      animateSatellites(0.016);

      const pos1 = new THREE.Vector3();
      const pos2 = new THREE.Vector3();
      getNodeLocalPosition("sat-P00S00", pos1);
      getNodeLocalPosition("sat-P05S10", pos2);

      const separation = pos1.distanceTo(pos2);
      expect(separation).toBeGreaterThan(10);
      expect(pos1.length()).toBeCloseTo(pos2.length(), 0);
    });

    it("positionCache is updated by animateSatellites", () => {
      const epoch = "2026-04-01T00:00:00Z";
      const epochUnix = new Date(epoch).getTime() / 1000;

      const ephemeris: SessionEphemeris = {
        epoch_id: 1,
        sim_time: epoch,
        epoch_unix: epochUnix,
        nodes: {
          "sat-P00S00": {
            type: "keplerian",
            altitude_km: 550,
            inclination_deg: 53,
            raan_deg: 45,
            true_anomaly_deg: 90,
            plane: 0,
            slot: 0,
          },
        },
      };

      const nodes = [makeSatNode("sat-P00S00", 0, 0, 550)];
      updateSatellites(nodes, earthFrame, "area", epoch);

      const cacheBefore = getPositionCache();
      const entry = getSatellites().get("sat-P00S00")!;
      const idx = entry.instanceIndex * 3;
      const xBefore = cacheBefore[idx]!;

      setEphemeris(ephemeris);
      onSnapshot(epoch, performance.now());
      animateSatellites(0.016);

      const xAfter = cacheBefore[idx]!;
      // Propagation from orbital elements should produce a different
      // position than the initial NodeState lat/lon placement
      expect(xAfter).not.toBeCloseTo(xBefore, 1);
    });
  });
});
