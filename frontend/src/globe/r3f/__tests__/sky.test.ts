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
});
