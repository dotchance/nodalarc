import { describe, it, expect, beforeEach } from "vitest";
import * as THREE from "three";
import { getSatellites } from "./satellites";
import type { SatelliteEntry } from "./satellites";
import { updateOrbitalTrails, clearTrails } from "./orbitalTrails";

/** Build a minimal SatelliteEntry for testing trail sampling. */
function makeSatEntry(meshPos: THREE.Vector3, currPos: THREE.Vector3): SatelliteEntry {
  const mesh = new THREE.Mesh(
    new THREE.SphereGeometry(0.01),
    new THREE.MeshBasicMaterial(),
  );
  mesh.position.copy(meshPos);

  const glow = new THREE.Sprite(new THREE.SpriteMaterial());
  glow.position.copy(meshPos);

  return {
    mesh,
    glow,
    prevPosition: meshPos.clone(),
    currPosition: currPos.clone(),
    snapshotTime: performance.now(),
    interval: 1000,
    nodeState: { node_id: "sat-P00S00", node_type: "satellite" } as any,
  };
}

function findTrailLine(scene: THREE.Scene): THREE.Line {
  const line = scene.children.find(
    (c) => c instanceof THREE.Line,
  ) as THREE.Line | undefined;
  expect(line).toBeDefined();
  return line!;
}

describe("orbitalTrails", () => {
  let scene: THREE.Scene;

  beforeEach(() => {
    scene = new THREE.Scene();
    getSatellites().clear();
    clearTrails();
  });

  it("samples from mesh position (post-lerp), not currPosition (target)", () => {
    const sats = getSatellites();

    // Satellite is visually at (1, 0, 0) but lerping toward (2, 0, 0)
    const meshPos = new THREE.Vector3(1, 0, 0);
    const targetPos = new THREE.Vector3(2, 0, 0);
    sats.set("sat-P00S00", makeSatEntry(meshPos, targetPos));

    // Run enough frames for at least one sample (SAMPLE_EVERY = 2)
    for (let i = 0; i < 4; i++) {
      updateOrbitalTrails(scene);
    }

    const trailLine = findTrailLine(scene);
    const posAttr = trailLine.geometry.getAttribute("position") as THREE.BufferAttribute;
    const drawRange = trailLine.geometry.drawRange;
    expect(drawRange.count).toBeGreaterThan(0);

    // The newest trail point (last in draw order) should match mesh position,
    // NOT the currPosition target.
    const lastIdx = (drawRange.count - 1) * 3;
    const trailX = posAttr.array[lastIdx]!;
    const trailY = posAttr.array[lastIdx + 1]!;
    const trailZ = posAttr.array[lastIdx + 2]!;

    // Trail must match mesh position (1, 0, 0), not target (2, 0, 0)
    expect(trailX).toBeCloseTo(meshPos.x, 5);
    expect(trailY).toBeCloseTo(meshPos.y, 5);
    expect(trailZ).toBeCloseTo(meshPos.z, 5);

    // Explicitly verify it does NOT match the target position
    expect(trailX).not.toBeCloseTo(targetPos.x, 1);
  });

  it("trail stays behind the satellite, never ahead", () => {
    const sats = getSatellites();

    // Simulate a satellite moving along +X axis
    const entry = makeSatEntry(
      new THREE.Vector3(1.0, 0, 0),
      new THREE.Vector3(1.1, 0, 0),
    );
    sats.set("sat-P00S00", entry);

    // Accumulate trail points by advancing the mesh position
    for (let step = 0; step < 20; step++) {
      const x = 1.0 + step * 0.01;
      entry.mesh.position.set(x, 0, 0);
      entry.currPosition.set(x + 0.1, 0, 0); // target is always ahead
      updateOrbitalTrails(scene);
    }

    const trailLine = findTrailLine(scene);
    const posAttr = trailLine.geometry.getAttribute("position") as THREE.BufferAttribute;
    const count = trailLine.geometry.drawRange.count;

    // Every trail point's X should be <= the current mesh X
    const currentMeshX = entry.mesh.position.x;
    for (let i = 0; i < count; i++) {
      const px = posAttr.array[i * 3]!;
      expect(px).toBeLessThanOrEqual(currentMeshX + 1e-6);
    }
  });
});
