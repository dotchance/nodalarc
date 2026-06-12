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

// Engine-declared wall-ms-per-sim-ms (1/rate); null when unavailable.
let _declaredWallMsPerSimMs: number | null = null;

// --- Display continuity layer ---------------------------------------------
// The anchors (_lastSimTimeMs/_lastSimWallTime) track the ENGINE's phase —
// authoritative, rebased on every arrival. The display layer renders a
// monotonic, slewed view of that phase: during continuous playback the
// displayed time NEVER steps backward. Arrival jitter used to convert
// directly into backward display steps (the renderer extrapolates ahead;
// a late snapshot rebased the phase below the displayed value), visible
// as satellites snapping back every few seconds — violent at 60x where
// 100 ms of lateness is 6 sim-seconds. Instead the display clock runs
// within ±MAX_SLEW_FRACTION of the nominal rate until it re-converges
// with the engine phase (NTP-style slew). Genuine discontinuities —
// seek, resume, session reset, or a phase error worth more than
// SNAP_WALL_ERROR_MS of wall time (e.g. a backgrounded tab) — snap the
// display: those are real jumps and smoothing them would be a false
// state display.
const MAX_SLEW_FRACTION = 0.25;
const SNAP_WALL_ERROR_MS = 1000;

let _dispSimMs: number | null = null;
let _dispWallMs: number | null = null;

function _resetDisplay(): void {
  _dispSimMs = null;
  _dispWallMs = null;
}

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
  declaredRate?: number | null,
): { simDeltaMs: number } | null {
  // Frequency from the engine's own declaration (measured delivered rate,
  // falling back to commanded speed), phase from arrivals. Inferring
  // frequency by differentiating arrival spacing aliases against the tick
  // rate and converts delivery jitter into accumulated clock error — the
  // sim/wall divergence at 1x this replaced. Inference remains only as a
  // fallback for snapshots lacking the declared fields.
  if (declaredRate != null && declaredRate > 0) {
    _declaredWallMsPerSimMs = 1 / declaredRate;
  } else {
    _declaredWallMsPerSimMs = null;
  }
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
      _resetDisplay();
      return null;
    }
    return null;
  }

  const simDeltaMs = simTimeMs - _lastSimTimeMs;
  const wallDelta = now - (_lastSimWallTime as number);

  // Large forward jump — seek discontinuity. Re-seed immediately rather
  // than waiting for 3 outlier rejections. "Large" is judged against the
  // sim advance the CURRENT rate predicts for this wall interval — an
  // absolute threshold is rate-blind: at 60x every normal arrival
  // carries 60 s of sim per 1 s of wall and would be misread as a seek,
  // hard-reseeding the clock on every snapshot (the 60x jerk).
  const knownRate = _declaredWallMsPerSimMs ?? (_rateSeeded ? _wallMsPerSimMs : null);
  const expectedSimDeltaMs = knownRate !== null ? wallDelta / knownRate : 0;
  if (simDeltaMs - expectedSimDeltaMs > 30_000 && wallDelta < 5_000) {
    _lastSimTimeMs = simTimeMs;
    _lastSimWallTime = now;
    _wallMsPerSimMs = DEFAULT_WALL_MS_PER_SIM_MS;
    _rateSeeded = false;
    _consecutiveOutliers = 0;
    _resetDisplay();
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
  return _declaredWallMsPerSimMs ?? _wallMsPerSimMs;
}

/** Interpolated sim_time at the given wall-clock instant, in ms since epoch.
 *
 *  Returns null if the clock has not been seeded (no snapshot received yet).
 *  When paused (via setPlaybackPaused), returns the frozen value — no
 *  extrapolation past the freeze point (d(sim)/d(wall) = 0 while paused).
 *
 *  @param now  performance.now()
 */
export function interpolatedSimTimeMs(now: number): number | null {
  if (_frozen) return _frozenSimTimeMs;
  if (_lastSimTimeMs === null || _lastSimWallTime === null) return null;
  const rate = _declaredWallMsPerSimMs ?? _wallMsPerSimMs;
  const target = _lastSimTimeMs + (now - _lastSimWallTime) / rate;

  if (_dispSimMs === null || _dispWallMs === null || now < _dispWallMs) {
    _dispSimMs = target;
    _dispWallMs = now;
    return target;
  }
  const nominal = (now - _dispWallMs) / rate;
  // Phase error measured against where pure nominal advance would land —
  // measuring against the pre-advance position over-corrects by one
  // frame's nominal and the display then leads the engine forever.
  const errorSimMs = target - (_dispSimMs + nominal);
  if (Math.abs(errorSimMs * rate) > SNAP_WALL_ERROR_MS) {
    // Real discontinuity (or a long render stall): snap, don't smooth.
    _dispSimMs = target;
    _dispWallMs = now;
    return target;
  }
  const bound = MAX_SLEW_FRACTION * nominal;
  const correction = Math.max(-bound, Math.min(bound, errorSimMs));
  // Total advance ∈ [0.75, 1.25]·nominal ≥ 0 — monotonic by construction.
  _dispSimMs += nominal + correction;
  _dispWallMs = now;
  return _dispSimMs;
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
    // The display anchors to the frozen value too — resume is a real
    // discontinuity in wall time, never slewed.
    if (_frozenSimTimeMs !== null) {
      _lastSimTimeMs = _frozenSimTimeMs;
      _lastSimWallTime = performance.now();
      _dispSimMs = _frozenSimTimeMs;
      _dispWallMs = _lastSimWallTime;
    }
    _frozen = false;
    _frozenSimTimeMs = null;
  }
}

/** Reset the clock. Call on session switch so stale EMA doesn't bleed
 *  into the new session's rate estimate. */
export function resetSimClock(): void {
  _declaredWallMsPerSimMs = null;
  _wallMsPerSimMs = DEFAULT_WALL_MS_PER_SIM_MS;
  _rateSeeded = false;
  _lastSimTimeMs = null;
  _lastSimWallTime = null;
  _consecutiveOutliers = 0;
  _frozen = false;
  _frozenSimTimeMs = null;
  _resetDisplay();
}
