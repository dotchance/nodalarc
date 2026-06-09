// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Pure orbit-ring sampling shared by the R3F orbit overlays. */

import * as THREE from "three";

export const ORBIT_SAMPLES = 180;
export const STATIC_ORBIT_RING_ECCENTRICITY_LIMIT = 1e-6;

/** Static ring overlays are only truthful for circular/near-circular Keplerian paths. */
export function supportsStaticOrbitRing(eccentricity: number | null | undefined): boolean {
  if (eccentricity == null) return false;
  return Math.abs(eccentricity) <= STATIC_ORBIT_RING_ECCENTRICITY_LIMIT;
}

/** Compute a closed great-circle orbit ring from position and velocity vectors. */
export function computeOrbitPositions(
  pos: THREE.Vector3,
  vel: THREE.Vector3,
  samples = ORBIT_SAMPLES,
): Float32Array {
  const normal = new THREE.Vector3().crossVectors(pos, vel).normalize();
  const radius = pos.length();
  const positions = new Float32Array((samples + 1) * 3);
  const q = new THREE.Quaternion();

  for (let i = 0; i <= samples; i++) {
    const angle = (i * 2 * Math.PI) / samples;
    q.setFromAxisAngle(normal, angle);
    const p = pos.clone().normalize().multiplyScalar(radius).applyQuaternion(q);
    positions[i * 3] = p.x;
    positions[i * 3 + 1] = p.y;
    positions[i * 3 + 2] = p.z;
  }

  return positions;
}
