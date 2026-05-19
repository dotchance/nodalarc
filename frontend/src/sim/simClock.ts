// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Shared sim-time clock for the VF.
 *
 *  Tracks the relationship between wall-clock time and backend sim_time
 *  as an EMA of `wallMsPerSimMs`. Both satellite position interpolation
 *  and Earth-frame rotation consume from this single authoritative source
 *  so the two motions remain in lockstep.
 *
 *  Semantics preserved verbatim from the EMA previously inline in
 *  satellites.ts (see git history):
 *    - On first snapshot: seed lastSimTime + lastWallTime, no EMA update.
 *    - On each subsequent snapshot where sim_time advances: compute
 *      instantaneous rate, clamp outliers (0.2 < ratio < 5.0), update EMA.
 *    - Minimum wallDelta of 10ms required to update EMA (noise floor).
 *    - EMA alpha 0.15.
 *
 *  State is module-scoped. A VF has exactly one backend connection and
 *  one sim timeline at a time; the session-switch path calls
 *  resetSimClock() to clear between sessions.
 */

const RATE_EMA_ALPHA = 0.15;
const MIN_WALL_DELTA_MS = 10;
const OUTLIER_RATIO_MIN = 0.2;
const OUTLIER_RATIO_MAX = 5.0;
const DEFAULT_WALL_MS_PER_SIM_MS = 1.0;
/** After this many consecutive outlier-clamped snapshots, accept the new
 *  rate as a legitimate persistent change (e.g. user changed playback
 *  speed from 1x to 30x) and re-seed the EMA. Standard adaptive outlier
 *  filter pattern: reject transient jitter, accept sustained step changes. */
const RESEED_AFTER_CONSECUTIVE_OUTLIERS = 3;

let _wallMsPerSimMs = DEFAULT_WALL_MS_PER_SIM_MS;
let _rateSeeded = false;
let _lastSimTimeMs: number | null = null;
let _lastSimWallTime: number | null = null;
let _consecutiveOutliers = 0;

// Pause state: when frozen, interpolatedSimTimeMs returns a constant.
// d(sim)/d(wall) = 0 means NO extrapolation past the freeze point.
let _frozen = false;
let _frozenSimTimeMs: number | null = null;

/** Record a new snapshot arrival. Updates the wall-to-sim rate EMA.
 *
 *  @param simTimeIso  ISO-8601 sim_time string from the snapshot
 *  @param now         performance.now() of snapshot arrival
 *  @returns  { simDeltaMs } — sim-time advance since previous snapshot,
 *            or null if this is the first snapshot (seed only) or
 *            sim_time did not advance (duplicate).
 */
export function onSnapshot(
  simTimeIso: string,
  now: number,
): { simDeltaMs: number } | null {
  const simTimeMs = new Date(simTimeIso).getTime();

  if (_lastSimTimeMs === null) {
    // First snapshot — seed and return.
    _lastSimTimeMs = simTimeMs;
    _lastSimWallTime = now;
    return null;
  }

  if (simTimeMs <= _lastSimTimeMs) {
    // Small regression or duplicate — ignore (jitter).
    // Large backward jump — seek discontinuity. Re-seed immediately.
    const backwardMs = _lastSimTimeMs - simTimeMs;
    if (backwardMs > 5000) {
      // Seek backward detected: re-seed clock at new sim_time.
      _lastSimTimeMs = simTimeMs;
      _lastSimWallTime = now;
      _wallMsPerSimMs = DEFAULT_WALL_MS_PER_SIM_MS;
      _rateSeeded = false;
      _consecutiveOutliers = 0;
      return null;
    }
    return null;
  }

  const simDeltaMs = simTimeMs - _lastSimTimeMs;
  const wallDelta = now - (_lastSimWallTime as number);

  // Large forward jump — seek discontinuity. Re-seed immediately
  // rather than waiting for 3 outlier rejections.
  if (simDeltaMs > 30_000 && wallDelta < 5_000) {
    _lastSimTimeMs = simTimeMs;
    _lastSimWallTime = now;
    _wallMsPerSimMs = DEFAULT_WALL_MS_PER_SIM_MS;
    _rateSeeded = false;
    _consecutiveOutliers = 0;
    return null;
  }

  if (wallDelta > MIN_WALL_DELTA_MS) {
    const instantRate = wallDelta / simDeltaMs;
    if (!_rateSeeded) {
      _wallMsPerSimMs = instantRate;
      _rateSeeded = true;
    } else {
      const ratio = instantRate / _wallMsPerSimMs;
      if (ratio > OUTLIER_RATIO_MIN && ratio < OUTLIER_RATIO_MAX) {
        _wallMsPerSimMs =
          _wallMsPerSimMs * (1 - RATE_EMA_ALPHA) + instantRate * RATE_EMA_ALPHA;
        _consecutiveOutliers = 0;
      } else {
        // Outlier: could be transient jitter OR a legitimate persistent
        // rate change (user changed playback speed). Track consecutive
        // outliers; if sustained, re-seed EMA to the new reality.
        _consecutiveOutliers++;
        if (_consecutiveOutliers >= RESEED_AFTER_CONSECUTIVE_OUTLIERS) {
          _wallMsPerSimMs = instantRate;
          _consecutiveOutliers = 0;
        }
      }
    }
  }

  _lastSimTimeMs = simTimeMs;
  _lastSimWallTime = now;
  return { simDeltaMs };
}

/** Current EMA-smoothed rate of wall-ms per sim-ms.
 *  Used by satellite lerp to convert sim-time intervals to wall-time. */
export function wallMsPerSimMs(): number {
  return _wallMsPerSimMs;
}

/** Interpolated sim_time at the given wall-clock instant, in ms since epoch.
 *
 *  Returns null if the clock has not been seeded (no snapshot received yet).
 *  When paused (via setPlaybackPaused), returns the frozen value — no
 *  extrapolation past the freeze point (d(sim)/d(wall) = 0 per R-OME-008B).
 *
 *  @param now  performance.now()
 */
export function interpolatedSimTimeMs(now: number): number | null {
  if (_frozen) return _frozenSimTimeMs;
  if (_lastSimTimeMs === null || _lastSimWallTime === null) return null;
  const wallSinceLast = now - _lastSimWallTime;
  return _lastSimTimeMs + wallSinceLast / _wallMsPerSimMs;
}

/** Notify simClock of playback pause/resume.
 *
 *  On pause: freeze interpolatedSimTimeMs at its current value.
 *  On resume: reset the wall-time reference so extrapolation starts
 *  from "now" at the frozen sim_time — no catch-up burst.
 */
export function setPlaybackPaused(paused: boolean): void {
  if (paused && !_frozen) {
    _frozenSimTimeMs = interpolatedSimTimeMs(performance.now());
    _frozen = true;
  } else if (!paused && _frozen) {
    // Resume: anchor simClock at the frozen value with wall_time = now.
    // Next interpolation starts from this point, no gap from pause duration.
    if (_frozenSimTimeMs !== null) {
      _lastSimTimeMs = _frozenSimTimeMs;
      _lastSimWallTime = performance.now();
    }
    _frozen = false;
    _frozenSimTimeMs = null;
  }
}

/** Reset the clock. Call on session switch so stale EMA doesn't bleed
 *  into the new session's rate estimate. */
export function resetSimClock(): void {
  _wallMsPerSimMs = DEFAULT_WALL_MS_PER_SIM_MS;
  _rateSeeded = false;
  _lastSimTimeMs = null;
  _lastSimWallTime = null;
  _consecutiveOutliers = 0;
  _frozen = false;
  _frozenSimTimeMs = null;
}
