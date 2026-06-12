// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.

import { describe, expect, it } from "vitest";
import { EARTH_RADIUS_RENDER } from "../units";
import { starShellRadiusForCameraFar, sunDirectionForDate } from "../Earth";

describe("r3f sky background", () => {
  it("keeps the star shell inside the camera far plane", () => {
    const far = 10_000;

    const radius = starShellRadiusForCameraFar(far);

    expect(radius).toBeGreaterThan(EARTH_RADIUS_RENDER * 50);
    expect(radius).toBeLessThan(far);
  });

  it("scales the star shell with multi-body camera envelopes", () => {
    const far = 80_000;

    expect(starShellRadiusForCameraFar(far)).toBe(36_000);
  });

  it("rejects impossible camera clipping inputs", () => {
    expect(() => starShellRadiusForCameraFar(0)).toThrow(/invalid camera far plane/);
    expect(() => starShellRadiusForCameraFar(Number.POSITIVE_INFINITY)).toThrow(
      /invalid camera far plane/,
    );
  });

  it("computes a normalized sun reference direction", () => {
    const direction = sunDirectionForDate(new Date("2026-06-03T14:17:20Z"));

    expect(direction.length()).toBeCloseTo(1, 12);
  });

  it("rejects invalid sun-reference dates", () => {
    expect(() => sunDirectionForDate(new Date("not-a-date"))).toThrow(/invalid date/);
  });

  it("tracks real solar declination: solstices and equinox", () => {
    // y = sin(declination). June solstice 2026 ≈ +23.43°, December ≈ −23.43°,
    // March equinox ≈ 0. Spencer's series is good to ~0.3°.
    const june = sunDirectionForDate(new Date("2026-06-21T12:00:00Z"));
    expect(Math.asin(june.y) * (180 / Math.PI)).toBeCloseTo(23.43, 0);
    const december = sunDirectionForDate(new Date("2026-12-21T12:00:00Z"));
    expect(Math.asin(december.y) * (180 / Math.PI)).toBeCloseTo(-23.43, 0);
    const equinox = sunDirectionForDate(new Date("2026-03-20T12:00:00Z"));
    expect(Math.abs(Math.asin(equinox.y) * (180 / Math.PI))).toBeLessThan(0.6);
  });

  it("models the equation of time: subsolar longitude at 12:00 UTC", () => {
    // Early November the sundial runs ~16.4 min fast: at 12:00 UTC the
    // subsolar point sits ~4.1° west of Greenwich. Mid-February it runs
    // ~14 min slow: ~3.6° east. (Hour angle = atan2(z, x), west-positive.)
    const november = sunDirectionForDate(new Date("2026-11-03T12:00:00Z"));
    const novemberDeg = Math.atan2(november.z, november.x) * (180 / Math.PI);
    expect(novemberDeg).toBeGreaterThan(3.4);
    expect(novemberDeg).toBeLessThan(4.8);
    const february = sunDirectionForDate(new Date("2026-02-11T12:00:00Z"));
    const februaryDeg = Math.atan2(february.z, february.x) * (180 / Math.PI);
    expect(februaryDeg).toBeLessThan(-3.0);
    expect(februaryDeg).toBeGreaterThan(-4.2);
  });
});
