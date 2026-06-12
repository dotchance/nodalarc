// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** InstancedMesh raycast staleness — the contract Constellation depends on.
 *
 * Three caches InstancedMesh.boundingSphere on the first raycast and never
 * invalidates it when instance matrices change. After a session switch moves
 * satellites outside the cached sphere, every click ray misses the sphere
 * and the raycast skips all instances — satellite clicks go dead until a
 * page reload. Constellation nulls the sphere after each matrix write; this
 * test pins both the failure mode and the invalidation fix, so a Three
 * upgrade that changes the caching behavior surfaces here.
 */
import { describe, it, expect } from "vitest";
import * as THREE from "three";

function makeMesh(radius: number, count: number): THREE.InstancedMesh {
  const mesh = new THREE.InstancedMesh(
    new THREE.SphereGeometry(0.02, 8, 8),
    new THREE.MeshBasicMaterial(),
    100,
  );
  const m = new THREE.Matrix4();
  for (let i = 0; i < count; i++) {
    const angle = (i / count) * Math.PI * 2;
    m.makeTranslation(radius * Math.cos(angle), 0, radius * Math.sin(angle));
    mesh.setMatrixAt(i, m);
  }
  mesh.count = count;
  mesh.instanceMatrix.needsUpdate = true;
  return mesh;
}

function raycastHits(
  mesh: THREE.InstancedMesh,
  target: THREE.Vector3,
  origin: THREE.Vector3,
): number {
  const raycaster = new THREE.Raycaster();
  raycaster.set(origin, target.clone().sub(origin).normalize());
  const hits: THREE.Intersection[] = [];
  mesh.raycast(raycaster, hits);
  return hits.length;
}

describe("InstancedMesh raycast across constellation changes", () => {
  it("misses relocated instances while the cached sphere is stale, hits after invalidation", () => {
    const mesh = makeMesh(1.1, 8);
    mesh.updateMatrixWorld(true);

    // First raycast caches the bounding sphere over the small shell.
    const nearTarget = new THREE.Vector3(1.1, 0, 0);
    expect(raycastHits(mesh, nearTarget, nearTarget.clone().multiplyScalar(3))).toBeGreaterThan(0);
    expect(mesh.boundingSphere).not.toBeNull();

    // "Session switch": same mesh, satellites now on a much larger shell
    // (a different session's render scale moves them orders of magnitude).
    const m = new THREE.Matrix4();
    for (let i = 0; i < 8; i++) {
      const angle = (i / 8) * Math.PI * 2;
      m.makeTranslation(6 * Math.cos(angle), 0, 6 * Math.sin(angle));
      mesh.setMatrixAt(i, m);
    }
    mesh.instanceMatrix.needsUpdate = true;

    // A click whose ray does not graze the stale sphere — any satellite away
    // from the old shell's projected disk, e.g. viewed from above the pole —
    // misses every instance.
    const target = new THREE.Vector3(6, 0, 0);
    const overhead = new THREE.Vector3(6, 5, 0);
    expect(raycastHits(mesh, target, overhead)).toBe(0);

    // Constellation's fix: null the sphere after matrix writes.
    mesh.boundingSphere = null;
    expect(raycastHits(mesh, target, overhead)).toBeGreaterThan(0);
  });
});
