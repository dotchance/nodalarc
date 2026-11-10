// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
/** Body samples: the page's interpolation against the backend's states, and the store's
 * rules for message order, seek, recovery, reconnect, window replacement and fast playback. */
import { beforeEach, describe, expect, it } from "vitest";
import type { BodySamples } from "../../builder/generated/builderApi";
import {
  acceptBodySampleWindow,
  acceptEphemerisEpoch,
  displayableWindows,
  interpolateWindow,
  interpolateWindows,
  matMul,
  resetBodySamples,
  skyAt,
  transpose,
  windowCovers,
  windowEnd,
  windowStart,
  type Matrix3,
  type Vector3,
} from "../bodySamples";
import { BODY_SAMPLE_FIXTURE } from "./bodySampleFixture";

const { ephemeris, windows, bodies_truth: truth } = BODY_SAMPLE_FIXTURE;
const [first, second] = windows;

function distance(a: Vector3, b: Vector3): number {
  return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
}

function rotationAngle(a: Matrix3, b: Matrix3): number {
  const m = matMul(transpose(a), b);
  const sine = Math.hypot(m[2][1] - m[1][2], m[0][2] - m[2][0], m[1][0] - m[0][1]) / 2;
  return Math.atan2(sine, (m[0][0] + m[1][1] + m[2][2] - 1) / 2);
}

function withEpoch(window: BodySamples, epochId: number): BodySamples {
  return { ...window, epoch_id: epochId };
}

describe("interpolation against the backend's states", () => {
  it("covers instants between samples on both sides of the window replacement", () => {
    const inFirst = truth.filter((state) => windowCovers(first, state.sim_time_unix));
    const onlySecond = truth.filter((state) => !windowCovers(first, state.sim_time_unix));
    expect(inFirst.length).toBeGreaterThan(5);
    expect(onlySecond.length).toBeGreaterThan(5);
  });

  it("matches positions, velocities, rotations and angular velocities", () => {
    // Backend bounds at 600 s spacing: 1 mm for body positions, 1e-10 rad for
    // orientation; 10 mm for the Sun, whose data evaluate with 3 mm of noise.
    // That noise over the 600 s spacing puts the Sun's velocity within 2e-8 km/s
    // (measured 6.2e-9 km/s).
    for (const state of truth) {
      const sky = interpolateWindows(windows, state.sim_time_unix);
      expect(sky).not.toBeNull();
      if (!sky) continue;
      for (const [bodyId, expected] of Object.entries(state.bodies)) {
        const body = sky.bodies[bodyId];
        expect(body).toBeDefined();
        if (!body) continue;
        expect(distance(body.positionKm, expected.position_km)).toBeLessThan(1e-6);
        expect(distance(body.velocityKmS, expected.velocity_km_s)).toBeLessThan(1e-9);
        expect(rotationAngle(body.rotation, expected.rotation)).toBeLessThan(1e-10);
        expect(distance(body.angularVelocityRadS, expected.angular_velocity_rad_s)).toBeLessThan(
          1e-13,
        );
      }
      expect(distance(sky.sunPositionKm, state.sun.position_km)).toBeLessThan(1e-5);
      expect(distance(sky.sunVelocityKmS, state.sun.velocity_km_s)).toBeLessThan(2e-8);
    }
  });

  it("gives identical states from either window where both cover an instant", () => {
    const overlap = truth.filter(
      (state) => windowCovers(first, state.sim_time_unix) && windowCovers(second, state.sim_time_unix),
    );
    expect(overlap.length).toBeGreaterThan(0);
    for (const state of overlap) {
      expect(interpolateWindow(first, state.sim_time_unix)).toEqual(
        interpolateWindow(second, state.sim_time_unix),
      );
    }
  });

  it("returns nothing outside the window", () => {
    expect(interpolateWindow(first, windowStart(first) - 1)).toBeNull();
    expect(interpolateWindow(first, windowEnd(first) + 1)).toBeNull();
  });
});

describe("the store", () => {
  const epochUnix = ephemeris.epoch_unix;
  const renderTime = epochUnix + 60;

  beforeEach(() => resetBodySamples());

  it("shows nothing without a SessionEphemeris, whichever arrives first", () => {
    expect(acceptBodySampleWindow(first)).toBe("accepted");
    expect(skyAt(renderTime)).toBeNull();
    acceptEphemerisEpoch(ephemeris);
    expect(skyAt(renderTime)).not.toBeNull();

    resetBodySamples();
    acceptEphemerisEpoch(ephemeris);
    expect(skyAt(renderTime)).toBeNull();
    acceptBodySampleWindow(first);
    expect(skyAt(renderTime)).not.toBeNull();
  });

  it("after a seek, pairs the new epoch's ephemeris and window in either order", () => {
    acceptEphemerisEpoch(ephemeris);
    acceptBodySampleWindow(first);
    const seeked = withEpoch(second, ephemeris.epoch_id + 1);
    const seekedTime = windowStart(seeked) + 30;

    // Window first: held, not displayed until its ephemeris arrives.
    expect(acceptBodySampleWindow(seeked)).toBe("accepted");
    expect(displayableWindows()).toEqual([first]);
    acceptEphemerisEpoch({ epoch_id: ephemeris.epoch_id + 1, epoch_unix: windowStart(seeked) });
    expect(displayableWindows()).toEqual([seeked]);
    expect(skyAt(seekedTime)).not.toBeNull();
    // A late window of the old epoch is refused.
    expect(acceptBodySampleWindow(first)).toBe("older-epoch");
  });

  it("after a seek, shows nothing until the new epoch's window arrives", () => {
    acceptEphemerisEpoch(ephemeris);
    acceptBodySampleWindow(first);
    acceptEphemerisEpoch({ epoch_id: ephemeris.epoch_id + 1, epoch_unix: windowStart(second) });
    expect(skyAt(renderTime)).toBeNull();
    acceptBodySampleWindow(withEpoch(second, ephemeris.epoch_id + 1));
    expect(skyAt(windowStart(second) + 30)).not.toBeNull();
  });

  it("refuses an older window of the same epoch, as a recovered OME republishes", () => {
    acceptEphemerisEpoch(ephemeris);
    acceptBodySampleWindow(first);
    acceptBodySampleWindow(second);
    // OME restarts and republishes the same ephemeris and an earlier window.
    acceptEphemerisEpoch(ephemeris);
    expect(acceptBodySampleWindow(first)).toBe("older-window");
    expect(acceptBodySampleWindow(second)).toBe("duplicate");
    const recovered = windowStart(second) + 120;
    expect(skyAt(recovered)).not.toBeNull();
  });

  it("keeps the old window until the replacement covers the render time", () => {
    acceptEphemerisEpoch(ephemeris);
    acceptBodySampleWindow(first);
    acceptBodySampleWindow(second);
    const beforeReplacement = windowStart(second) - 300;
    expect(windowCovers(first, beforeReplacement)).toBe(true);
    expect(skyAt(beforeReplacement)).not.toBeNull();
    expect(displayableWindows()).toEqual([first, second]);
    expect(skyAt(windowStart(second) + 300)).not.toBeNull();
    expect(displayableWindows()).toEqual([second]);
  });

  it("shows nothing outside every window", () => {
    acceptEphemerisEpoch(ephemeris);
    acceptBodySampleWindow(first);
    expect(skyAt(windowEnd(first) + 1)).toBeNull();
  });

  it("recovers on reconnect from the latest ephemeris and window", () => {
    acceptEphemerisEpoch(ephemeris);
    acceptBodySampleWindow(first);
    resetBodySamples();
    acceptEphemerisEpoch(ephemeris);
    acceptBodySampleWindow(second);
    expect(skyAt(windowStart(second) + 600)).not.toBeNull();
  });

  it("holds the same state while paused", () => {
    acceptEphemerisEpoch(ephemeris);
    acceptBodySampleWindow(first);
    expect(skyAt(renderTime)).toEqual(skyAt(renderTime));
  });

  it("starts over for the same epoch with a different anchor, as a Builder preview does", () => {
    acceptEphemerisEpoch(ephemeris);
    acceptBodySampleWindow(second);
    acceptEphemerisEpoch({ epoch_id: ephemeris.epoch_id, epoch_unix: ephemeris.epoch_unix - 7200 });
    expect(displayableWindows()).toEqual([]);
    expect(acceptBodySampleWindow(first)).toBe("accepted");
  });

  it("stays available at 1,000 times real time across the window replacement", () => {
    // OME publishes the next window when sim time passes its due instant; the
    // page receives it up to 3.6 s of wall time (3,600 s of sim time) later and
    // renders every 1/60 s of wall time (16.7 s of sim time).
    acceptEphemerisEpoch(ephemeris);
    acceptBodySampleWindow(first);
    const due = windowStart(second) + 3600;
    const arrival = due + 3600;
    for (let t = epochUnix; t < windowEnd(second); t += 1000 / 60) {
      if (t >= arrival && displayableWindows().length === 1 && displayableWindows()[0] === first) {
        acceptBodySampleWindow(second);
      }
      expect(skyAt(t), `render time ${t}`).not.toBeNull();
    }
  });
});
