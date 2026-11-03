// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Where each body sits and how it is turned in the rendered world, from the body
 * samples at one instant.
 *
 * The world's axes are ICRF in the inertial view and Earth's body-fixed axes in
 * the Earth-fixed view; the origin is Earth's centre in both. `viewRotation`
 * maps ICRF axes to the world's axes (identity, or Earth's sampled orientation).
 * A body group holds the body's body-fixed frame, so its world rotation is
 * view · bodyᵀ, and its centre is view · position. The starfield is fixed on
 * ICRF axes, so it takes the view rotation itself.
 *
 * Render axes: a km vector (x, y, z) renders as (x, z, −y); a rotation G renders
 * as M·G·Mᵀ with M that axis map. Pure (no React).
 */

import * as THREE from "three";
import {
  matMul,
  matVec,
  transpose,
  type Matrix3,
  type SkyState,
  type Vector3,
} from "../../sim/bodySamples";
import type { ReferenceFrame } from "../../types";

const IDENTITY: Matrix3 = [
  [1, 0, 0],
  [0, 1, 0],
  [0, 0, 1],
];

/** km axes to render axes: (x, y, z) → (x, z, −y). */
const RENDER_AXES: Matrix3 = [
  [1, 0, 0],
  [0, 0, 1],
  [0, -1, 0],
];

export interface BodyPose {
  /** Body centre on world axes, km. */
  positionKm: Vector3;
  /** Body-fixed axes to world axes. */
  rotation: Matrix3;
  /** The body frame's angular velocity relative to the world, on body-fixed axes, rad/s. */
  angularVelocityLocalRadS: Vector3;
}

/**
 * ICRF axes to world axes. Null in the Earth-fixed view of a session whose
 * samples carry no Earth: that view has no axes to show.
 */
export function viewRotation(sky: SkyState, mode: ReferenceFrame): Matrix3 | null {
  if (mode === "earth-inertial") return IDENTITY;
  return sky.bodies.earth?.rotation ?? null;
}

export function bodyPose(sky: SkyState, bodyId: string, mode: ReferenceFrame): BodyPose | null {
  const body = sky.bodies[bodyId];
  const view = viewRotation(sky, mode);
  if (!body || !view) return null;
  const viewSpin: Vector3 =
    mode === "earth-inertial"
      ? [0, 0, 0]
      : (sky.bodies.earth?.angularVelocityRadS ?? [0, 0, 0]);
  const w = body.angularVelocityRadS;
  return {
    positionKm: matVec(view, body.positionKm),
    rotation: matMul(view, transpose(body.rotation)),
    angularVelocityLocalRadS: matVec(body.rotation, [
      w[0] - viewSpin[0],
      w[1] - viewSpin[1],
      w[2] - viewSpin[2],
    ]),
  };
}

/** The Sun's direction on world axes (unit vector, km axes). */
export function sunDirection(sky: SkyState, mode: ReferenceFrame): Vector3 | null {
  const view = viewRotation(sky, mode);
  if (!view) return null;
  const s = matVec(view, sky.sunPositionKm);
  const n = Math.hypot(s[0], s[1], s[2]);
  return [s[0] / n, s[1] / n, s[2] / n];
}

export function toRenderVector(v: Vector3, target: THREE.Vector3, scale = 1): THREE.Vector3 {
  return target.set(v[0] * scale, v[2] * scale, -v[1] * scale);
}

export function toRenderQuaternion(m: Matrix3, target: THREE.Quaternion): THREE.Quaternion {
  const r = matMul(matMul(RENDER_AXES, m), transpose(RENDER_AXES));
  const m4 = new THREE.Matrix4().set(
    r[0][0], r[0][1], r[0][2], 0,
    r[1][0], r[1][1], r[1][2], 0,
    r[2][0], r[2][1], r[2][2], 0,
    0, 0, 0, 1,
  );
  return target.setFromRotationMatrix(m4);
}

/** Ω × p_local + v_local, rotated to world axes: a body-local point's world velocity. */
export function worldVelocity(
  pLocal: THREE.Vector3,
  vLocal: THREE.Vector3,
  groupRotation: THREE.Quaternion,
  omegaLocalRender: THREE.Vector3,
  target: THREE.Vector3,
): THREE.Vector3 {
  return target.copy(omegaLocalRender).cross(pLocal).add(vLocal).applyQuaternion(groupRotation);
}

