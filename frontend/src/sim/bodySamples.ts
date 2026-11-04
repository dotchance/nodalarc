// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Body states from OME's published samples.
 *
 * The page has no model of how bodies move or turn. OME publishes BodySamples
 * windows (every session body and the Sun at instants 600 s apart, Earth-centred,
 * ICRF axes, Unix seconds, km, km/s, rad/s) and this module interpolates them:
 * positions with cubic Hermite polynomials from the position and velocity at
 * both ends, rotations at a constant rate about one axis between two samples,
 * angular velocity linearly. The backend measured the error of this scheme at
 * 600 s spacing: 0.1 mm for the Moon's position, 2.6 mm for Mars's (the data's
 * evaluation noise at 2.1e8 km, the same at every spacing), and at most
 * 2.9e-11 rad for any body's orientation (tests/unit/physics/test_body_samples.py).
 *
 * The store holds what the page may display. An epoch is displayable only with
 * its SessionEphemeris and a window of the same epoch: windows that arrive
 * before their ephemeris wait, windows of an older epoch are refused, and a
 * window that starts before the newest one of its epoch is refused. A window
 * is kept until a newer one covers the render time. Outside every window the
 * store returns null and the page shows that body positions are unavailable.
 *
 * No THREE, no DOM: the propagation worker imports this module.
 */

import type { BodySamples } from "../builder/generated/builderApi";

export type Vector3 = readonly [number, number, number];
/** A 3x3 matrix as three rows. */
export type Matrix3 = readonly [Vector3, Vector3, Vector3];

export interface BodyStateAt {
  positionKm: Vector3;
  velocityKmS: Vector3;
  /** Maps ICRF axes to the body-fixed frame: v_fixed = rotation · v_icrf. */
  rotation: Matrix3;
  /** On ICRF axes: a body-fixed velocity is rotation · (v − ω × r). */
  angularVelocityRadS: Vector3;
}

export interface SkyState {
  simTimeUnix: number;
  bodies: Readonly<Record<string, BodyStateAt>>;
  sunPositionKm: Vector3;
  sunVelocityKmS: Vector3;
}

// ---------------------------------------------------------------------------
// Vector and matrix helpers
// ---------------------------------------------------------------------------

export function matVec(m: Matrix3, v: Vector3): Vector3 {
  return [
    m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
    m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
    m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
  ];
}

/** mᵀ · v */
export function matTVec(m: Matrix3, v: Vector3): Vector3 {
  return [
    m[0][0] * v[0] + m[1][0] * v[1] + m[2][0] * v[2],
    m[0][1] * v[0] + m[1][1] * v[1] + m[2][1] * v[2],
    m[0][2] * v[0] + m[1][2] * v[1] + m[2][2] * v[2],
  ];
}

export function matMul(a: Matrix3, b: Matrix3): Matrix3 {
  const row = (r: Vector3): Vector3 => [
    r[0] * b[0][0] + r[1] * b[1][0] + r[2] * b[2][0],
    r[0] * b[0][1] + r[1] * b[1][1] + r[2] * b[2][1],
    r[0] * b[0][2] + r[1] * b[1][2] + r[2] * b[2][2],
  ];
  return [row(a[0]), row(a[1]), row(a[2])];
}

export function transpose(m: Matrix3): Matrix3 {
  return [
    [m[0][0], m[1][0], m[2][0]],
    [m[0][1], m[1][1], m[2][1]],
    [m[0][2], m[1][2], m[2][2]],
  ];
}

export function cross(a: Vector3, b: Vector3): Vector3 {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}

/** Rotation by `angle` about the unit vector `axis` (Rodrigues). */
function axisAngle(axis: Vector3, angle: number): Matrix3 {
  const [x, y, z] = axis;
  const c = Math.cos(angle);
  const s = Math.sin(angle);
  const t = 1 - c;
  return [
    [t * x * x + c, t * x * y - s * z, t * x * z + s * y],
    [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
    [t * x * z - s * y, t * y * z + s * x, t * z * z + c],
  ];
}

/** The rotation a fraction `s` of the way from r0 to r1, at a constant rate. */
function interpolateRotation(r0: Matrix3, r1: Matrix3, s: number): Matrix3 {
  const step = matMul(r1, transpose(r0));
  const vx = (step[2][1] - step[1][2]) / 2;
  const vy = (step[0][2] - step[2][0]) / 2;
  const vz = (step[1][0] - step[0][1]) / 2;
  const sine = Math.hypot(vx, vy, vz);
  // atan2 of sine and cosine: the cosine alone cannot resolve small angles.
  const angle = Math.atan2(sine, (step[0][0] + step[1][1] + step[2][2] - 1) / 2);
  if (sine === 0) return r0;
  return matMul(axisAngle([vx / sine, vy / sine, vz / sine], s * angle), r0);
}

function hermite(
  p0: Vector3, v0: Vector3, p1: Vector3, v1: Vector3, h: number, s: number,
): { position: Vector3; velocity: Vector3 } {
  const s2 = s * s;
  const s3 = s2 * s;
  const h00 = 2 * s3 - 3 * s2 + 1;
  const h10 = s3 - 2 * s2 + s;
  const h01 = -2 * s3 + 3 * s2;
  const h11 = s3 - s2;
  const d00 = 6 * s2 - 6 * s;
  const d10 = 3 * s2 - 4 * s + 1;
  const d01 = -6 * s2 + 6 * s;
  const d11 = 3 * s2 - 2 * s;
  const at = (q0: number, w0: number, q1: number, w1: number) =>
    h00 * q0 + h10 * h * w0 + h01 * q1 + h11 * h * w1;
  const rate = (q0: number, w0: number, q1: number, w1: number) =>
    (d00 * q0 + d01 * q1) / h + d10 * w0 + d11 * w1;
  return {
    position: [
      at(p0[0], v0[0], p1[0], v1[0]),
      at(p0[1], v0[1], p1[1], v1[1]),
      at(p0[2], v0[2], p1[2], v1[2]),
    ],
    velocity: [
      rate(p0[0], v0[0], p1[0], v1[0]),
      rate(p0[1], v0[1], p1[1], v1[1]),
      rate(p0[2], v0[2], p1[2], v1[2]),
    ],
  };
}

// ---------------------------------------------------------------------------
// Interpolation within one window
// ---------------------------------------------------------------------------

function sampleAt(window: BodySamples, index: number) {
  const sample = window.samples[index];
  if (sample === undefined) {
    throw new Error(`BodySamples has no sample ${index} of ${window.samples.length}`);
  }
  return sample;
}

export function windowStart(window: BodySamples): number {
  return sampleAt(window, 0).sim_time_unix;
}

export function windowEnd(window: BodySamples): number {
  return sampleAt(window, window.samples.length - 1).sim_time_unix;
}

export function windowCovers(window: BodySamples, t: number): boolean {
  return t >= windowStart(window) && t <= windowEnd(window);
}

/** Every body and the Sun at `t`, or null when the window does not cover `t`. */
export function interpolateWindow(window: BodySamples, t: number): SkyState | null {
  if (!windowCovers(window, t)) return null;
  const n = window.samples.length;
  const h = window.interval_s;
  let k = Math.floor((t - windowStart(window)) / h);
  if (k > n - 2) k = n - 2;
  const a = sampleAt(window, k);
  const b = sampleAt(window, k + 1);
  const s = (t - a.sim_time_unix) / h;
  const bodies: Record<string, BodyStateAt> = {};
  for (const [bodyId, start] of Object.entries(a.bodies)) {
    const end = b.bodies[bodyId as keyof typeof b.bodies];
    if (!start || !end) {
      throw new Error(`BodySamples sample ${k + 1} carries no state for ${bodyId}`);
    }
    const { position, velocity } = hermite(
      start.position_km, start.velocity_km_s, end.position_km, end.velocity_km_s, h, s,
    );
    const w0 = start.angular_velocity_rad_s;
    const w1 = end.angular_velocity_rad_s;
    bodies[bodyId] = {
      positionKm: position,
      velocityKmS: velocity,
      rotation: interpolateRotation(start.rotation, end.rotation, s),
      angularVelocityRadS: [
        w0[0] + s * (w1[0] - w0[0]),
        w0[1] + s * (w1[1] - w0[1]),
        w0[2] + s * (w1[2] - w0[2]),
      ],
    };
  }
  const sun = hermite(
    a.sun.position_km, a.sun.velocity_km_s, b.sun.position_km, b.sun.velocity_km_s, h, s,
  );
  return { simTimeUnix: t, bodies, sunPositionKm: sun.position, sunVelocityKmS: sun.velocity };
}

/**
 * The state at `t` from the newest of `windows` that covers it. Windows of
 * one epoch share the sample grid, so any covering window gives the same
 * values; the newest is taken so older windows can be released.
 */
export function interpolateWindows(windows: readonly BodySamples[], t: number): SkyState | null {
  for (const window of [...windows].reverse()) {
    const state = interpolateWindow(window, t);
    if (state) return state;
  }
  return null;
}

// ---------------------------------------------------------------------------
// The store: what the page may display
// ---------------------------------------------------------------------------

interface EpochIdentity {
  epoch_id: number;
  epoch_unix: number;
}

let _epoch: EpochIdentity | null = null;
/** Windows the store holds, in arrival order of acceptance (oldest first). */
let _windows: BodySamples[] = [];
let _version = 0;
const _listeners = new Set<() => void>();

function changed(): void {
  _version += 1;
  for (const listener of _listeners) listener();
}

/** Bumped whenever the displayable windows change; the worker bridge resends on change, and
 *  React reads it through useSyncExternalStore so render-time reads recompute. */
export function bodySamplesVersion(): number {
  return _version;
}

export function subscribeBodySamples(listener: () => void): () => void {
  _listeners.add(listener);
  return () => {
    _listeners.delete(listener);
  };
}

export function resetBodySamples(): void {
  _epoch = null;
  _windows = [];
  changed();
}

/**
 * Record the SessionEphemeris the page now propagates satellites from.
 *
 * A newer epoch releases older windows and keeps any of its own that arrived
 * first. The same epoch with a different anchor is a different lineage (a
 * Builder preview of an edited session), so its windows start over. The same
 * epoch and anchor again (OME republishes after it recovers) changes nothing.
 */
export function acceptEphemerisEpoch(ephemeris: EpochIdentity): void {
  if (_epoch === null || ephemeris.epoch_id > _epoch.epoch_id) {
    _epoch = { epoch_id: ephemeris.epoch_id, epoch_unix: ephemeris.epoch_unix };
    _windows = _windows.filter((window) => window.epoch_id >= ephemeris.epoch_id);
    changed();
    return;
  }
  if (ephemeris.epoch_id === _epoch.epoch_id && ephemeris.epoch_unix !== _epoch.epoch_unix) {
    _epoch = { epoch_id: ephemeris.epoch_id, epoch_unix: ephemeris.epoch_unix };
    _windows = [];
    changed();
  }
}

export type WindowVerdict = "accepted" | "duplicate" | "older-epoch" | "older-window";

export function acceptBodySampleWindow(window: BodySamples): WindowVerdict {
  if (_epoch !== null && window.epoch_id < _epoch.epoch_id) return "older-epoch";
  const sameEpoch = _windows.filter((held) => held.epoch_id === window.epoch_id);
  const newestStart = sameEpoch.reduce(
    (latest, held) => Math.max(latest, windowStart(held)),
    Number.NEGATIVE_INFINITY,
  );
  const start = windowStart(window);
  if (start < newestStart) return "older-window";
  if (start === newestStart) {
    // Two windows produced within one sample interval share their start; the
    // later one may reach one sample further and replaces the held one.
    const held = sameEpoch.find((candidate) => windowStart(candidate) === start);
    if (held === undefined || windowEnd(window) <= windowEnd(held)) return "duplicate";
    _windows = _windows.map((candidate) => (candidate === held ? window : candidate));
    changed();
    return "accepted";
  }
  _windows = [..._windows, window];
  changed();
  return "accepted";
}

/** The windows of the displayed epoch, oldest first. Empty until the pair exists. */
export function displayableWindows(): readonly BodySamples[] {
  if (_epoch === null) return [];
  const epochId = _epoch.epoch_id;
  return _windows.filter((window) => window.epoch_id === epochId);
}

/**
 * Every body and the Sun at `t` without releasing any window: for reads during a
 * React render, which must not change the store.
 */
export function peekSkyAt(t: number): SkyState | null {
  return interpolateWindows(displayableWindows(), t);
}

/**
 * Every body and the Sun at render time `t`, or null when the displayed epoch
 * has no window covering `t`. Once the newest window covers `t`, the older
 * windows of the epoch are released.
 */
export function skyAt(t: number): SkyState | null {
  const windows = displayableWindows();
  if (windows.length === 0) return null;
  const newest = windows[windows.length - 1];
  if (newest !== undefined && windows.length > 1 && windowCovers(newest, t)) {
    _windows = _windows.filter((window) => window.epoch_id !== newest.epoch_id || window === newest);
    changed();
  }
  return interpolateWindows(windows, t);
}
