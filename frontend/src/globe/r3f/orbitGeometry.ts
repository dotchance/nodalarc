// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
/** Pure orbit-ring sampling shared by the R3F orbit overlays. */

import * as THREE from "three";

export const ORBIT_SAMPLES = 180;
export const STATIC_ORBIT_RING_ECCENTRICITY_LIMIT = 1e-6;

/** Static ring overlays are only truthful for circular/near-circular Keplerian paths. */
export function supportsStaticOrbitRing(eccentricity: number | null | undefined): boolean {
  if (eccentricity == null) return false;
  return Math.abs(eccentricity) <= STATIC_ORBIT_RING_ECCENTRICITY_LIMIT;
}

/**
 * Compute a closed great-circle orbit ring from a satellite's world position, its velocity
 * relative to its central body, and that body's world centre. The ring turns about the body's
 * centre and its vertices are relative to that centre, in world axes: the world is centred on
 * whichever body the view is on and the body moves in it, so a ring is drawn where its body is
 * now by placing its group at the body's centre each frame.
 */
export function computeOrbitPositions(
  pos: THREE.Vector3,
  vel: THREE.Vector3,
  center: THREE.Vector3,
  samples = ORBIT_SAMPLES,
): Float32Array {
  const radial = pos.clone().sub(center);
  const normal = new THREE.Vector3().crossVectors(radial, vel).normalize();
  const positions = new Float32Array((samples + 1) * 3);
  const q = new THREE.Quaternion();

  for (let i = 0; i <= samples; i++) {
    const angle = (i * 2 * Math.PI) / samples;
    q.setFromAxisAngle(normal, angle);
    const p = radial.clone().applyQuaternion(q);
    positions[i * 3] = p.x;
    positions[i * 3 + 1] = p.y;
    positions[i * 3 + 2] = p.z;
  }

  return positions;
}
