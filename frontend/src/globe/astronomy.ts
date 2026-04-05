// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** Astronomy helpers for reference-frame rendering.
 *
 *  Provides the earth-rotation angle (GMST) as a pure function of UTC, and
 *  a world-velocity helper that accounts for the frame rotation's effect on
 *  velocity (the ω × r term in the ECEF → ECI transform).
 *
 *  Contract: gmstRadians() MUST match the backend's services/ome/propagator.py
 *  gmst() function to machine precision for equivalent inputs. See the
 *  astronomy.test.ts contract tests.
 */

import * as THREE from "three";

/** Unix seconds for the J2000 epoch (2000-01-01 12:00:00 UTC). */
export const J2000_UNIX_SECONDS = 946728000.0;

/** Earth's sidereal angular velocity in radians per second.
 *  Matches services/ome/propagator.py:EARTH_ROTATION_RATE. */
export const EARTH_ROTATION_RATE_RAD_S = 7.2921159e-5;

/** Greenwich Mean Sidereal Time in radians for a given UTC timestamp.
 *
 *  Direct port of backend propagator.gmst(). Uses the simplified Vallado
 *  expression (GMST-1982 relative to the true equator/equinox of date).
 *  Accurate to arcseconds over decades — more than sufficient for visual
 *  rendering.
 *
 *  @param unixSeconds UTC timestamp in Unix seconds (float, fractional OK)
 *  @returns GMST angle in radians, normalized to [0, 2π)
 */
export function gmstRadians(unixSeconds: number): number {
  const daysSinceJ2000 = (unixSeconds - J2000_UNIX_SECONDS) / 86400.0;
  const degrees = 280.46061837 + 360.98564736629 * daysSinceJ2000;
  // JS % of float can be negative for negative dividends; Python's always
  // has the sign of the divisor (positive). Normalize to [0, 360).
  let wrapped = degrees % 360.0;
  if (wrapped < 0) wrapped += 360.0;
  return (wrapped * Math.PI) / 180.0;
}

/** Helper: parse an ISO-8601 sim-time string to Unix seconds.
 *  Returns NaN on parse failure (caller must handle). */
export function simTimeIsoToUnixSeconds(simTimeIso: string): number {
  return new Date(simTimeIso).getTime() / 1000;
}

/** Compute a satellite's world-frame velocity, given its earthFrame-local
 *  position and ECEF-derived scene-unit velocity, under the current
 *  earthFrame rotation angle AND angular velocity.
 *
 *  Derivation (see specs/eci-view-plan.md §1.6):
 *    p_world(t) = R_z(θ(t)) · p_local(t)
 *    v_world(t) = R_z(θ) · ((dθ/dt)·ẑ × p_local + v_local)
 *               = R_z(θ) · (Ω × p_local + v_local)
 *
 *  where Ω is the view-frame's angular velocity vector about +Y (Earth's
 *  rotation axis in Three.js Y-up coords).
 *
 *  For earth-fixed view: θ = 0 constant, dθ/dt = 0 → v_world = v_local.
 *  For earth-inertial view: θ = gmst(t), dθ/dt = Earth's sidereal rate.
 *
 *  We take dθ/dt as an explicit parameter rather than inferring it from
 *  θ, because the same θ value carries different semantics in the two
 *  modes (and because θ == 0 can occur transiently in the inertial view
 *  when gmst wraps through zero). The explicit signature generalizes to
 *  future rotating frames (moonFrame, Earth-Sun barycenter, etc.).
 *
 *  @param pLocal                sat's local (ECEF) position in scene units
 *  @param vEcefSceneUnits       sat's ECEF velocity in scene units/s
 *  @param viewFrameRotationRad  current earthFrame.rotation.y (radians)
 *  @param frameAngularVelocityRadS  dθ/dt: 0 for static frame,
 *                                   EARTH_ROTATION_RATE_RAD_S for earth-inertial
 *  @param target                output vector (avoids allocation)
 *  @returns target, filled with world-frame velocity in scene units/s
 */
export function worldVelocity(
  pLocal: THREE.Vector3,
  vEcefSceneUnits: THREE.Vector3,
  viewFrameRotationRad: number,
  frameAngularVelocityRadS: number,
  target: THREE.Vector3,
): THREE.Vector3 {
  // Ω × p_local + v_local, in the local (ECEF) frame.
  // Ω = (0, frameAngularVelocityRadS, 0)
  // Ω × (x, y, z) = (Ω_y·z, 0, -Ω_y·x)
  const w = frameAngularVelocityRadS;
  const lx = w * pLocal.z + vEcefSceneUnits.x;
  const ly = vEcefSceneUnits.y;
  const lz = -w * pLocal.x + vEcefSceneUnits.z;
  // R_z(θ) about +Y: (x,z) → (x·cos θ + z·sin θ, -x·sin θ + z·cos θ)
  const c = Math.cos(viewFrameRotationRad);
  const s = Math.sin(viewFrameRotationRad);
  target.set(lx * c + lz * s, ly, -lx * s + lz * c);
  return target;
}
