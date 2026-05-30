// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Scene-unit conversion: Earth radius is the render-space yardstick. */

import { describe, it, expect } from "vitest";
import { EARTH_RADIUS_KM, RENDER_UNITS_PER_KM, kmToRender } from "../units";

describe("scene units", () => {
  it("one Earth radius is one render unit", () => {
    expect(kmToRender(EARTH_RADIUS_KM)).toBeCloseTo(1.0);
  });

  it("a 550 km LEO shell sits just above the surface in render units", () => {
    expect(kmToRender(EARTH_RADIUS_KM + 550)).toBeCloseTo(1 + 550 * RENDER_UNITS_PER_KM);
  });

  it("scales linearly from zero", () => {
    expect(kmToRender(0)).toBe(0);
    expect(kmToRender(2 * EARTH_RADIUS_KM)).toBeCloseTo(2.0);
  });
});
